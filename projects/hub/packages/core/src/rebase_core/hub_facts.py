"""What the hub itself knows about a freelancer, as one source of their card (REB-838,
spec § 2.1 «Hub data»).

Two kinds of fact, both the hub's own and both hard: the admin's seniority and years
override (REB-799), and the engagements the person has really worked through the hub
(role, months, how it ended). Nothing else is given to the model: **never the client's
name** (the role an engagement is filed under is read with the client's name taken out
of it), the client's budget, the match's notes or the thread of admin comments.

Only an engagement that started counts as work: a draft, one in signature and a cancelled
one never did, and one with no start date cannot be placed in time, so it is left out
rather than guessed. `completed_months` is what `anni` may add from the hub: the months
covered by completed engagements, overlapping ones counted once.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.card_guard import STOPLIST
from rebase_core.errors import NotFound
from rebase_core.models import Company, Freelancer, Match

Esito = Literal["completato", "in corso", "chiuso prima"]

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
    (up to today for one `in corso`), how it ended, and the month it started."""

    ruolo: str
    mesi: int
    esito: Esito
    dal: str  # "YYYY-MM"


@dataclass(frozen=True)
class HubFacts:
    seniority_admin: str | None
    anni_admin: int | None
    matches: list[MatchFact] = field(default_factory=list)
    completed_months: int = 0

    def text(self) -> str:
        """The source as the model reads it: the admin's override first, as a fact that
        outranks any reading of the other sources, then the engagements."""
        lines = [HEADER]
        if self.seniority_admin is not None:
            lines.append(f"Seniority stabilita da un admin: {self.seniority_admin} (dato certo)")
        if self.anni_admin is not None:
            lines.append(
                f"Anni di esperienza stabiliti da un admin: {self.anni_admin} (dato certo)"
            )
        if self.matches:
            lines.append("Incarichi svolti tramite la hub (ruolo, inizio, durata, esito):")
            lines.extend(
                f"- {m.ruolo}, dal {m.dal}, {_months(m.mesi)}, {m.esito}" for m in self.matches
            )
        if self.completed_months:
            lines.append(f"Mesi di incarichi completati: {self.completed_months}")
        if len(lines) == 1:
            lines.append("Nessun dato.")
        return "\n".join(lines)

    def hash_material(self) -> str:
        """`text()` without what moves by itself: the months of an engagement still
        `in corso` grow every month and would mark the card stale each time."""
        if not self.seniority_admin and self.anni_admin is None and not self.matches:
            return ""
        return "\n".join(
            [
                f"seniority={self.seniority_admin}",
                f"anni={self.anni_admin}",
                *(
                    f"{m.dal}|{m.ruolo}|{m.esito}|{'' if m.esito == 'in corso' else m.mesi}"
                    for m in self.matches
                ),
                f"completati={self.completed_months}",
            ]
        )


def _months(mesi: int) -> str:
    return "meno di un mese" if mesi == 0 else f"{mesi} mes{'e' if mesi == 1 else 'i'}"


def _span_months(days: int) -> int:
    return max(0, round(days / _DAYS_PER_MONTH))


def _role(figura: str, clients: list[str]) -> str:
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
        figura = re.sub(rf"(?<!\w){re.escape(variant)}(?!\w)", _CLIENT, figura, flags=re.I)
    return re.sub(r"\s+", " ", figura).strip()


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
    `in corso` is measured to."""
    freelancer = session.get(Freelancer, freelancer_id)
    if freelancer is None:
        raise NotFound("Freelancer", freelancer_id)
    today = today or utcnow().date()
    rows = session.execute(
        select(Match, Company)
        .join(Company, Company.id == Match.company_id)
        .where(
            Match.freelancer_id == freelancer_id,
            Match.stato.in_(("attivo", "concluso")),
            Match.lettera_data_inizio.is_not(None),
        )
        .order_by(Match.lettera_data_inizio, Match.created_at)
    ).all()

    facts: list[MatchFact] = []
    completed: list[tuple[date, date]] = []
    for match, company in rows:
        start = match.lettera_data_inizio
        if start is None:  # the query asks for a start: this is the type's word, not a case
            continue
        planned = match.lettera_data_fine
        if match.stato == "attivo":
            esito: Esito = "in corso"
            end = min(today, planned) if planned is not None else today
        else:
            closed = match.updated_at.date()
            if planned is not None and closed < planned:
                esito, end = "chiuso prima", closed
            else:
                esito, end = "completato", planned if planned is not None else closed
        end = max(end, start)
        facts.append(
            MatchFact(
                ruolo=_role(
                    company.figura_richiesta, [company.nome_azienda, match.cliente_ragione_sociale]
                ),
                mesi=_span_months((end - start).days + 1),
                esito=esito,
                dal=f"{start:%Y-%m}",
            )
        )
        if esito == "completato":
            completed.append((start, end))
    return HubFacts(
        seniority_admin=freelancer.seniority_admin,
        anni_admin=freelancer.anni_admin,
        matches=facts,
        completed_months=_span_months(_union_days(completed)),
    )
