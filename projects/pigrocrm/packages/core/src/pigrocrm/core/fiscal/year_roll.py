"""The year boundary of the fiscal packs (REB-1005, taxes design § 1).

An srl used to run on `it-standard`, the ordinary regime's pack, because no pack of its own
existed. Existing srl entities move to `it-company` on the next 1 January and never in the
middle of a year: a year's invoices, advances and readings belong to one pack, and the year
that ends keeps the one it was lived under (every reading freezes its own pack, and
`FiscalProfileService.upsert` keeps the estimates of the pack it leaves before it moves). The
move is one profile save by the system, with the profile's own parameters unchanged, since
the two packs invoice the same way.

**What makes an entity an srl** is its name. The profile of a space is not a fiscal fact of
an entity (a space can hold an individual on the ordinary regime beside an srl), and the
entity carries no legal form, so the one thing that says it is the company name, which an
srl must make contain «società a responsabilità limitata» (art. 2463, n. 2, of the civil
code, Gazzetta Ufficiale text, read 2026-11-10), written in full or as «S.r.l.» / «srl».
An entity whose name does not say it stays where it is and the owner moves it by hand in the
settings. A deactivated entity does not move.

It runs inside the daily `pigrocrm automations` pass, which is already scheduled, walks
every space and is watched for lateness; a separate cron line would be one more thing to
forget. The pass acts on 1 January only, so a rehearsal of another day (`--date`) says what
that day would do and writes nothing.
"""

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import PACK_IT_COMPANY, PACK_IT_STANDARD
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.legal_entities.models import LegalEntity

# «S.r.l.», «srl», «S. r. l.», «s.r.l.s.» or the form in full, as a word of its own: «Israel»
# and «Sorrel» do not match.
_SRL_NAME = re.compile(
    r"(?<![a-z])s\.?\s*r\.?\s*l\.?(?:\s*s\.?)?(?![a-z])|responsabilit[aà]\s+limitata",
    re.IGNORECASE,
)


def is_srl_name(legal_name: str) -> bool:
    return _SRL_NAME.search(legal_name) is not None


@dataclass(frozen=True)
class YearRollReport:
    """`moved` is how many legal entities left `it-standard` for `it-company`, or would
    have in a rehearsal; `failed` how many a save refused; `due` whether the day is one
    that moves any."""

    due: bool
    moved: int
    failed: int = 0


def is_year_boundary(day: date) -> bool:
    return (day.month, day.day) == (1, 1)


class FiscalYearRoll:
    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, *, today: date, dry_run: bool) -> YearRollReport:
        if not is_year_boundary(today):
            return YearRollReport(due=False, moved=0)
        actor = Actor.system()
        bind_scope(self.session, actor)
        rows = self.session.execute(
            select(LegalEntity.id, LegalEntity.legal_name)
            .join(FiscalProfile, FiscalProfile.legal_entity_id == LegalEntity.id)
            .where(FiscalProfile.pack_id == PACK_IT_STANDARD, LegalEntity.active.is_(True))
            .order_by(FiscalProfile.created_at, FiscalProfile.id)
        ).all()
        candidates: list[UUID] = [row.id for row in rows if is_srl_name(row.legal_name)]
        if dry_run:
            return YearRollReport(due=True, moved=len(candidates))
        moved = 0
        failed = 0
        for legal_entity_id in candidates:
            # One entity's refusal (a profile somebody edited into a state the check now
            # rejects) must not leave the others on the old pack for another year.
            try:
                self._move(legal_entity_id, actor)
                moved += 1
            except Exception:  # noqa: BLE001 - counted, reported, and the rest still move
                self.session.rollback()
                failed += 1
        return YearRollReport(due=True, moved=moved, failed=failed)

    def _move(self, legal_entity_id: UUID, actor: Actor) -> None:
        service = FiscalProfileService(self.session)
        current = service.get(actor, legal_entity_id)
        fields = current.model_dump(include=set(FiscalProfileUpsert.model_fields) - {"updated_at"})
        service.upsert(
            FiscalProfileUpsert.model_validate({**fields, "pack_id": PACK_IT_COMPANY}),
            actor,
            legal_entity_id,
        )


__all__ = ["FiscalYearRoll", "YearRollReport", "is_srl_name", "is_year_boundary"]
