"""The writer of `legal_entity_sites` (REB-1092): the places of business of a legal entity beside
its registered office, which the IRAP estimate splits the base between (art. 4 c. 2 D.Lgs.
446/1997, `fiscal/irap_sites.py`).

A site is a fact about the company, not an agent's to move: every write is an administrator's and
`update_legal_entity_sites` is person-only in `agent_actions.py`. A site with staff assigned is not
removed; the people are moved first.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.irap_sites import irap_regions
from pigrocrm.core.legal_entities.models import LegalEntity, LegalEntitySite
from pigrocrm.core.legal_entities.site_schemas import (
    IrapRegionOption,
    LegalEntitySiteCreate,
    LegalEntitySiteRead,
    LegalEntitySiteUpdate,
)
from pigrocrm.core.schemas import supplied_changes
from pigrocrm.core.staff.models import StaffMember

ENTITY = "legal_entity_site"
PARENT = "legal_entity"
ACTION = "update_legal_entity_sites"


class LegalEntitySiteService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.activities = ActivityService(session)

    def list_irap_regions(self) -> list[IrapRegionOption]:
        """The regions a site can be in, in the order the pack lists them."""
        return [IrapRegionOption(key=key, name=name) for key, name in irap_regions()]

    def list_legal_entity_sites(
        self, legal_entity_id: UUID, actor: Actor
    ) -> list[LegalEntitySiteRead]:
        """The sites of one legal entity, with how many people work at each; the region and the
        city decide the order."""
        self._require_legal_entity(legal_entity_id)
        counts = {
            site_id: count
            for site_id, count in self.session.execute(
                select(StaffMember.site_id, func.count())
                .where(
                    StaffMember.legal_entity_id == legal_entity_id,
                    StaffMember.site_id.is_not(None),
                )
                .group_by(StaffMember.site_id)
            )
        }
        rows = self.session.execute(
            select(LegalEntitySite)
            .where(LegalEntitySite.legal_entity_id == legal_entity_id)
            .order_by(LegalEntitySite.region, LegalEntitySite.city, LegalEntitySite.id)
        ).scalars()
        return [self._read(row, counts.get(row.id, 0)) for row in rows]

    def add_legal_entity_site(
        self, legal_entity_id: UUID, data: LegalEntitySiteCreate, actor: Actor
    ) -> LegalEntitySiteRead:
        actor.require_admin(ACTION)
        self._require_legal_entity(legal_entity_id)
        self._require_region(data.region)
        row = LegalEntitySite(legal_entity_id=legal_entity_id, **data.model_dump())
        self.session.add(row)
        self.session.flush()
        self.activities.record(
            PARENT, legal_entity_id, "legal_entity_site_added", actor, {"region": row.region}
        )
        self.session.commit()
        self.session.refresh(row)
        return self._read(row, 0)

    def update_legal_entity_site(
        self, legal_entity_id: UUID, site_id: UUID, data: LegalEntitySiteUpdate, actor: Actor
    ) -> LegalEntitySiteRead:
        actor.require_admin(ACTION)
        row = self._require_site(legal_entity_id, site_id)
        changes = supplied_changes(data)
        if "region" in changes:
            if changes["region"] is None:
                raise ValidationFailed(
                    ENTITY, "region", "la regione non può essere svuotata", expected="una regione"
                )
            self._require_region(changes["region"])
        for key, value in changes.items():
            setattr(row, key, value)
        self.session.flush()
        self.activities.record(
            PARENT, legal_entity_id, "legal_entity_site_updated", actor, {"region": row.region}
        )
        self.session.commit()
        self.session.refresh(row)
        return self._read(row, self._staff_count(row.id))

    def remove_legal_entity_site(self, legal_entity_id: UUID, site_id: UUID, actor: Actor) -> None:
        """Takes a site away, once nobody works at it: a person assigned to it is moved first
        (409 with how many)."""
        actor.require_admin(ACTION)
        row = self._require_site(legal_entity_id, site_id)
        assigned = self._staff_count(row.id)
        if assigned:
            raise Conflict(
                ENTITY,
                "la sede ha ancora del personale assegnato: spostalo prima",
                site_id=str(site_id),
                staff_count=assigned,
            )
        region = row.region
        self.session.delete(row)
        self.session.flush()
        self.activities.record(
            PARENT, legal_entity_id, "legal_entity_site_removed", actor, {"region": region}
        )
        self.session.commit()

    def _require_legal_entity(self, legal_entity_id: UUID) -> None:
        if self.session.get(LegalEntity, legal_entity_id) is None:
            raise NotFound(PARENT, str(legal_entity_id))

    def _require_site(self, legal_entity_id: UUID, site_id: UUID) -> LegalEntitySite:
        row = self.session.get(LegalEntitySite, site_id)
        if row is None or row.legal_entity_id != legal_entity_id:
            raise NotFound(ENTITY, str(site_id))
        return row

    def _require_region(self, region: str) -> None:
        keys = [key for key, _ in irap_regions()]
        if region not in keys:
            raise ValidationFailed(
                ENTITY,
                "region",
                "non è una regione dell'elenco IRAP",
                expected="un codice ISTAT di regione, oppure BZ o TN",
            )

    def _staff_count(self, site_id: UUID) -> int:
        return (
            self.session.scalar(
                select(func.count()).select_from(StaffMember).where(StaffMember.site_id == site_id)
            )
            or 0
        )

    def _read(self, row: LegalEntitySite, staff_count: int) -> LegalEntitySiteRead:
        names = dict(irap_regions())
        read = LegalEntitySiteRead.model_validate(row)
        return read.model_copy(
            update={"region_name": names.get(row.region), "staff_count": staff_count}
        )


__all__ = ["LegalEntitySiteService"]
