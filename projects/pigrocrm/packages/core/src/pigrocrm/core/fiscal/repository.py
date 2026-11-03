from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.models import FiscalProfile


class FiscalProfileRepository:
    """A repository never commits (project rule): every method here reads or flushes,
    and the surrounding service method is the one transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, legal_entity_id: UUID) -> FiscalProfile | None:
        """The profile of one legal entity: at most one, by the unique key on
        `legal_entity_id`, and none until the person saves it once (spec §1.2)."""
        return (
            self.session.execute(
                select(FiscalProfile).where(FiscalProfile.legal_entity_id == legal_entity_id)
            )
            .scalars()
            .first()
        )

    def add(self, profile: FiscalProfile) -> FiscalProfile:
        self.session.add(profile)
        self.session.flush()
        return profile
