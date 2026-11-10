from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.models import FiscalEstimateReading, FiscalProfile


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


class FiscalEstimateRepository:
    """The frozen readings of `fiscal_estimates`. Never commits, like every repository."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, legal_entity_id: UUID, tax_year: int) -> FiscalEstimateReading | None:
        return (
            self.session.execute(
                select(FiscalEstimateReading).where(
                    FiscalEstimateReading.legal_entity_id == legal_entity_id,
                    FiscalEstimateReading.tax_year == tax_year,
                )
            )
            .scalars()
            .first()
        )

    def save(
        self,
        legal_entity_id: UUID,
        tax_year: int,
        *,
        pack_id: str,
        pack_version: str,
        law_from_year: int,
        parameters: dict[str, Any],
        figures: dict[str, Any],
    ) -> None:
        """One row per legal entity and year, replaced by the next reading while the regime
        lasts. An upsert and not a read-then-write: two requests computing the same year at
        once both land, and the second says what the first said."""
        values = {
            "pack_id": pack_id,
            "pack_version": pack_version,
            "law_from_year": law_from_year,
            "parameters": parameters,
            "figures": figures,
        }
        self.session.execute(
            pg_insert(FiscalEstimateReading)
            .values(legal_entity_id=legal_entity_id, tax_year=tax_year, **values)
            .on_conflict_do_update(
                constraint="uq_fiscal_estimates_year",
                set_={**values, "updated_at": func.now()},
            )
        )
