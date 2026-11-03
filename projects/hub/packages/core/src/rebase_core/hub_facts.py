"""What the hub itself knows about a freelancer, as one source of their card (REB-838,
spec § 2.1 «Hub data»).

Two kinds of fact, both the hub's own and both hard: the admin's seniority and years
override (REB-799), and the engagements the person has really worked through the hub
(role, months, how it ended). Nothing else is given to the model: **never the client's
name** (the role an engagement is filed under is read with the client's name taken out
of it), the client's budget, the match's notes or the thread of admin notes.

Only an engagement that started counts as work: a draft, one in signature and a cancelled
one never did, and one with no start date cannot be placed in time, so it is left out
rather than guessed. `completed_months` is what `experience_years` may add from the hub: the months
covered by completed engagements, overlapping ones counted once.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.card_guard import STOPLIST
from rebase_core.errors import NotFound
from rebase_core.models import AdminAction, Company, Freelancer, Match

Outcome = Literal["completed", "in_progress", "closed_before"]

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
    dal: str  # "YYYY-MM"


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
                f"Anni di esperienza stabiliti da un admin: {self.experience_years_admin} (dato certo)"
            )
        if self.matches:
            lines.append("Incarichi svolti tramite la hub (ruolo, inizio, durata, esito):")
            lines.extend(
                f"- {m.role}, dal {m.dal}, {_months(m.months)}, {m.outcome}" for m in self.matches
            )
        if self.completed_months:
            lines.append(f"Mesi di incarichi completati: {self.completed_months}")
        if len(lines) == 1:
            lines.append("Nessun dato.")
        return "\n".join(lines)

    def hash_material(self) -> str:
        """`text()` without what moves by itself: the months of an engagement still
        `in_progress` grow every month and would mark the card stale each time."""
        if self.empty:
            return ""
        return "\n".join(
            [
                f"seniority={self.seniority_admin}",
                f"anni={self.experience_years_admin}",
                *(
                    f"{m.dal}|{m.role}|{m.outcome}|{'' if m.outcome == 'in_progress' else m.months}"
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
    """`hub_facts` for several people in three queries (the people, their engagements, the
    days an admin closed them) instead of three per person: the same facts for each, which
    the stale list's batched read relies on. A person who does not exist is not in the answer."""
    today = today or utcnow().date()
    wanted = list(dict.fromkeys(freelancer_ids))
    if not wanted:
        return {}
    people = {
        row.id: (row.seniority_admin, row.experience_years_admin)
        for row in session.execute(
            select(
                Freelancer.id, Freelancer.seniority_admin, Freelancer.experience_years_admin
            ).where(Freelancer.id.in_(wanted))
        )
    }
    rows = session.execute(
        select(Match, Company)
        .join(Company, Company.id == Match.company_id)
        .where(
            Match.freelancer_id.in_(list(people)),
            Match.status.in_(("active", "concluded")),
            Match.letter_start_date.is_not(None),
        )
        .order_by(Match.letter_start_date, Match.created_at)
    ).all()

    # The day an admin closed each engagement is in the trail, and does not move when the
    # match is edited afterwards, as `updated_at` does; it is the fallback only for a match
    # closed before the trail kept it.
    closed_on = {
        entity: stamp.date()
        for entity, stamp in session.execute(
            select(AdminAction.entity_id, func.max(AdminAction.created_at))
            .where(
                AdminAction.entity_type == "match",
                AdminAction.kind == "match_closed",
                AdminAction.entity_id.in_([match.id for match, _ in rows]),
            )
            .group_by(AdminAction.entity_id)
        )
    }
    engagements: dict[UUID, list[tuple[Match, Company]]] = {person: [] for person in people}
    for match, company in rows:
        engagements[match.freelancer_id].append((match, company))
    return {
        person: _facts_of(people[person], engagements[person], closed_on, today)
        for person in people
    }


def _facts_of(
    person: tuple[str | None, int | None],
    rows: list[tuple[Match, Company]],
    closed_on: dict[UUID, date],
    today: date,
) -> HubFacts:
    facts: list[MatchFact] = []
    completed: list[tuple[date, date]] = []
    clients: set[str] = set()
    for match, company in rows:
        start = match.letter_start_date
        if start is None:  # the query asks for a start: this is the type's word, not a case
            continue
        planned = match.letter_end_date
        if match.status == "active":
            outcome: Outcome = "in_progress"
            end = min(today, planned) if planned is not None else today
        else:
            closed = closed_on.get(match.id) or match.updated_at.date()
            if planned is not None and closed < planned:
                outcome, end = "closed_before", closed
            else:
                outcome, end = "completed", planned if planned is not None else closed
        end = max(end, start)
        clients.update(
            name.strip()
            for name in (company.company_name, match.customer_legal_name)
            if name and name.strip()
        )
        facts.append(
            MatchFact(
                role=_role(
                    company.requested_role, [company.company_name, match.customer_legal_name]
                ),
                months=_span_months((end - start).days + 1),
                outcome=outcome,
                dal=f"{start:%Y-%m}",
            )
        )
        if outcome == "completed":
            completed.append((start, end))
    return HubFacts(
        seniority_admin=person[0],
        experience_years_admin=person[1],
        matches=facts,
        completed_months=_span_months(_union_days(completed)),
        clients=sorted(clients),
    )
