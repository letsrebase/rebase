"""What the hub itself knows about a freelancer, as one source of their card (REB-838,
spec § 2.1 «Hub data»).

Two kinds of fact, both the hub's own and both hard: the admin's seniority and years
override (REB-799), and the engagements the person has really worked through the hub
(role, months, how it ended), read from the read model the CRM's notices keep. Nothing else is
given to the model: **never the client's name** (the role an engagement is filed under is read
with the client's name taken out of it), the client's budget, the match's notes or the thread of
admin notes.

Only an engagement that started counts as work: a draft, one in signature and a cancelled
one never did, and one with no start date cannot be placed in time, so it is left out
rather than guessed. `completed_months` is what `experience_years` may add from the hub: the months
covered by completed engagements, overlapping ones counted once.

The approved work profile (REB-1134, `work_profiles/source.py`) is read here too, as
`work_profile`: its text, when the `work_profile` switch of `ai_sources` is on and the profile is
`approved`, has not expired and passes the anonymity guard. It is a source of its own (key
`work_profile`, its own hash), not part of `empty`, `text()` or `hash_material()`: those are what
every stored card's `source_hashes["hub"]` was made from and a changed byte would mark every card
stale.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.card_guard import STOPLIST, KnownIdentifiers
from rebase_core.errors import NotFound
from rebase_core.models import AiSource, Company, Freelancer, HubEngagement, User, WorkProfile
from rebase_core.sources import github_login_of, link_hostnames
from rebase_core.work_profiles.source import source_text

Outcome = Literal["completed", "in_progress", "closed_before"]
# The words an outcome has in the block the model reads and in the hash material: they are
# what every stored card's `source_hashes["hub"]` was made from, so they stay as they were.
OUTCOME_WORDS: dict[Outcome, str] = {
    "completed": "completato",
    "in_progress": "in corso",
    "closed_before": "chiuso prima",
}

HEADER = "FONTE: dati della hub"
_DAYS_PER_MONTH = 30.4375
# What stands where the client's name was: the role is kept, the client is not.
_CLIENT = "il cliente"
_LEGAL = re.compile(
    r"\b(?:s\.?r\.?l\.?|s\.?p\.?a\.?|s\.?a\.?s\.?|s\.?n\.?c\.?|ltd|inc|llc|gmbh)\b\.?", re.I
)


@dataclass(frozen=True)
class MatchFact:
    """One engagement: the role as the client's request names it, whole months worked
    (up to today for one `in_progress`), how it ended, and the month it started."""

    role: str
    months: int
    outcome: Outcome
    since: str  # "YYYY-MM"


@dataclass(frozen=True)
class HubFacts:
    seniority_admin: str | None
    experience_years_admin: int | None
    matches: list[MatchFact] = field(default_factory=list)
    completed_months: int = 0
    # The names of the clients the engagements were for: given to the anonymity guard and
    # to nothing else (not `text()`, not `hash_material()`, not the prompt), so a form of a
    # name the role's scrub missed still cannot reach a card.
    clients: list[str] = field(default_factory=list)
    # The approved work profile as the prompt reads it, or `None` (switch off, no profile, not
    # approved, expired, or the guard found the person in it). Its own source and hash.
    work_profile: str | None = None

    @property
    def empty(self) -> bool:
        """Nothing to tell the model: no override and no engagement that started."""
        return not self.seniority_admin and self.experience_years_admin is None and not self.matches

    def text(self) -> str:
        """The source as the model reads it: the admin's override first, as a fact that
        outranks any reading of the other sources, then the engagements."""
        lines = [HEADER]
        if self.seniority_admin is not None:
            lines.append(f"Seniority stabilita da un admin: {self.seniority_admin} (dato certo)")
        if self.experience_years_admin is not None:
            lines.append(
                "Anni di esperienza stabiliti da un admin: "
                f"{self.experience_years_admin} (dato certo)"
            )
        if self.matches:
            lines.append("Incarichi svolti tramite la hub (ruolo, inizio, durata, esito):")
            lines.extend(
                f"- {m.role}, dal {m.since}, {_months(m.months)}, {OUTCOME_WORDS[m.outcome]}"
                for m in self.matches
            )
        if self.completed_months:
            lines.append(f"Mesi di incarichi completati: {self.completed_months}")
        if len(lines) == 1:
            lines.append("Nessun dato.")
        return "\n".join(lines)

    def hash_material(self) -> str:
        """`text()` without what moves by itself: the months of an engagement still
        in progress grow every month and would mark the card stale each time. The keys and
        the outcome words are the ones the hash was first made from, since a changed byte
        marks every stored card stale."""
        if self.empty:
            return ""
        return "\n".join(
            [
                f"seniority={self.seniority_admin}",
                f"anni={self.experience_years_admin}",
                *(
                    f"{m.since}|{m.role}|{OUTCOME_WORDS[m.outcome]}|"
                    f"{'' if m.outcome == 'in_progress' else m.months}"
                    for m in self.matches
                ),
                f"completati={self.completed_months}",
            ]
        )


def _months(months: int) -> str:
    return "meno di un mese" if months == 0 else f"{months} mes{'e' if months == 1 else 'i'}"


def _span_months(days: int) -> int:
    return max(0, round(days / _DAYS_PER_MONTH))


def _role(profile: str, clients: list[str]) -> str:
    """The role with the client's name out of it: whole, without its legal form, and each
    of its words but the ordinary ones, since a role names «Initech» for «Initech Italia
    srl». «Backend per Initech Italia srl» and «Backend per Initech» both say «Backend per
    il cliente»."""
    variants: set[str] = set()
    for name in clients:
        name = name.strip()
        if name:
            bare = _LEGAL.sub("", name).strip(" .,")
            variants.update({name, bare})
            variants.update(
                word for word in re.findall(r"[^\W_]{3,}", bare) if word.lower() not in STOPLIST
            )
    for variant in sorted((v for v in variants if v), key=len, reverse=True):
        profile = re.sub(rf"(?<!\w){re.escape(variant)}(?!\w)", _CLIENT, profile, flags=re.I)
    return re.sub(r"\s+", " ", profile).strip()


def _union_days(spans: list[tuple[date, date]]) -> int:
    total, reach = 0, None
    for start, end in sorted(spans):
        if reach is None or start > reach:
            total += (end - start).days + 1
            reach = end
        elif end > reach:
            total += (end - reach).days
            reach = end
    return total


def hub_facts(session: Session, freelancer_id: UUID, today: date | None = None) -> HubFacts:
    """The hub's facts about the freelancer, read now. `today` is the day an engagement
    `in_progress` is measured to."""
    found = hub_facts_many(session, [freelancer_id], today)
    if freelancer_id not in found:
        raise NotFound("Freelancer", freelancer_id)
    return found[freelancer_id]


def hub_facts_many(
    session: Session, freelancer_ids: Sequence[UUID], today: date | None = None
) -> dict[UUID, HubFacts]:
    """`hub_facts` for several people in two queries (the people, their engagements) instead of
    two per person: the same facts for each, which the stale list's batched read relies on. The
    engagements are the hub's read model of the CRM's (`HubEngagement`, REB-1106), never the
    hub's own matches. A person who does not exist is not in the answer."""
    today = today or utcnow().date()
    wanted = list(dict.fromkeys(freelancer_ids))
    if not wanted:
        return {}
    # The work profile rides in the people's own query, so a read of the facts costs the same
    # statements with the switch on or off: the payload is read only while the switch is on, and
    # only of an approved profile that has not expired.
    switch_on = func.coalesce(
        select(AiSource.enabled).where(AiSource.kind == "work_profile").scalar_subquery(), False
    )
    people = {
        row.id: _Person(
            row.seniority_admin,
            row.experience_years_admin,
            row.last_name,
            list(row.links or []),
            row.work_profile,
        )
        for row in session.execute(
            select(
                Freelancer.id,
                Freelancer.seniority_admin,
                Freelancer.experience_years_admin,
                Freelancer.links,
                User.last_name,
                case((switch_on, WorkProfile.payload), else_=None).label("work_profile"),
            )
            .join(User, User.id == Freelancer.user_id)
            .outerjoin(
                WorkProfile,
                and_(
                    WorkProfile.freelancer_id == Freelancer.id,
                    WorkProfile.state == "approved",
                    WorkProfile.expires_at > utcnow(),
                ),
            )
            .where(Freelancer.id.in_(wanted))
        )
    }
    rows = session.execute(
        select(HubEngagement, Company.company_name, Company.requested_role)
        .outerjoin(Company, Company.id == HubEngagement.company_id)
        .where(
            HubEngagement.freelancer_id.in_(list(people)),
            HubEngagement.state.in_(("active", "concluded")),
            HubEngagement.starts_on.is_not(None),
        )
        .order_by(HubEngagement.starts_on, HubEngagement.created_at)
    ).all()
    engagements: dict[UUID, list[tuple[HubEngagement, str | None, str | None]]] = {
        person: [] for person in people
    }
    for engagement, company_name, requested_role in rows:
        engagements[engagement.freelancer_id].append((engagement, company_name, requested_role))
    return {person: _facts_of(people[person], engagements[person], today) for person in people}


@dataclass(frozen=True)
class _Person:
    """What `hub_facts_many` reads of one person beside their engagements."""

    seniority_admin: str | None
    experience_years_admin: int | None
    last_name: str
    links: list[str]
    work_profile: dict[str, Any] | None


def _facts_of(
    person: _Person,
    rows: list[tuple[HubEngagement, str | None, str | None]],
    today: date,
) -> HubFacts:
    facts: list[MatchFact] = []
    completed: list[tuple[date, date]] = []
    clients: set[str] = set()
    for engagement, company_name, requested_role in rows:
        start = engagement.starts_on
        if start is None:  # the query asks for a start: this is the type's word, not a case
            continue
        planned = engagement.ends_on
        if engagement.state == "active":
            outcome: Outcome = "in_progress"
            end = min(today, planned) if planned is not None else today
        else:
            # Closing early moves `ends_on` back to the day it was closed (the CRM's rule); the
            # end it was planned to have is what the notices kept while it was live.
            end = planned if planned is not None else engagement.updated_at.date()
            before = engagement.planned_ends_on
            outcome = "closed_before" if before is not None and end < before else "completed"
        end = max(end, start)
        names = (company_name, engagement.client_name)
        clients.update(name.strip() for name in names if name and name.strip())
        facts.append(
            MatchFact(
                role=_role(
                    engagement.role or requested_role or "",
                    [name for name in names if name],
                ),
                months=_span_months((end - start).days + 1),
                outcome=outcome,
                since=f"{start:%Y-%m}",
            )
        )
        if outcome == "completed":
            completed.append((start, end))
    work_profile = None
    if person.work_profile:
        known = KnownIdentifiers(
            last_name=person.last_name,
            github_login=github_login_of(person.links),
            hostnames=link_hostnames(person.links),
            employers=sorted(clients),
        )
        work_profile = source_text(person.work_profile, known)
    return HubFacts(
        seniority_admin=person.seniority_admin,
        experience_years_admin=person.experience_years_admin,
        matches=facts,
        completed_months=_span_months(_union_days(completed)),
        clients=sorted(clients),
        work_profile=work_profile,
    )
