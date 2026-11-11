from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.models import (
    DeclaredTaxFigure,
    FiscalEstimateReading,
    FiscalProfile,
    TaxAdvanceMethod,
    TaxInstalmentPlan,
    TaxNoticePlan,
)


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


class DeclaredTaxFigureRepository:
    """The accountant's typed figures of `declared_tax_figures`. Never commits."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def for_year(self, legal_entity_id: UUID, tax_year: int) -> list[DeclaredTaxFigure]:
        return list(
            self.session.execute(
                select(DeclaredTaxFigure)
                .where(
                    DeclaredTaxFigure.legal_entity_id == legal_entity_id,
                    DeclaredTaxFigure.tax_year == tax_year,
                )
                .order_by(DeclaredTaxFigure.tax)
            ).scalars()
        )

    def save(
        self,
        legal_entity_id: UUID,
        tax: str,
        tax_year: int,
        *,
        tax_due: Decimal | None,
        balance: Decimal | None,
        advances: Decimal | None,
        source: str,
    ) -> DeclaredTaxFigure:
        """One row per legal entity, tax and year, replaced whole by the next typing. An
        upsert, so two administrators typing the same figure at once both land and the
        second one stands."""
        values = {"tax_due": tax_due, "balance": balance, "advances": advances, "source": source}
        row_id = self.session.execute(
            pg_insert(DeclaredTaxFigure)
            .values(legal_entity_id=legal_entity_id, tax=tax, tax_year=tax_year, **values)
            .on_conflict_do_update(
                constraint="uq_declared_tax_figures",
                set_={**values, "updated_at": func.now()},
            )
            .returning(DeclaredTaxFigure.id)
        ).scalar_one()
        row = self.session.get(DeclaredTaxFigure, row_id)
        assert row is not None
        self.session.refresh(row)
        return row


class TaxAdvanceMethodRepository:
    """The method chosen for the advances of a tax year, `tax_advance_methods`. Never commits."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def for_entity(self, legal_entity_id: UUID) -> dict[int, str]:
        """Every year the legal entity chose a method for, by tax year."""
        return {
            row.tax_year: row.method
            for row in self.session.execute(
                select(TaxAdvanceMethod).where(TaxAdvanceMethod.legal_entity_id == legal_entity_id)
            ).scalars()
        }

    def save(self, legal_entity_id: UUID, tax_year: int, method: str) -> TaxAdvanceMethod:
        """One row per legal entity and year, replaced by the next choice. An upsert, so two
        administrators choosing at once both land and the second one stands."""
        row_id = self.session.execute(
            pg_insert(TaxAdvanceMethod)
            .values(legal_entity_id=legal_entity_id, tax_year=tax_year, method=method)
            .on_conflict_do_update(
                constraint="uq_tax_advance_methods",
                set_={"method": method, "updated_at": func.now()},
            )
            .returning(TaxAdvanceMethod.id)
        ).scalar_one()
        row = self.session.get(TaxAdvanceMethod, row_id)
        assert row is not None
        self.session.refresh(row)
        return row


class TaxInstalmentPlanRepository:
    """The instalment plans of `tax_instalment_plans`. Never commits."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def for_entity(self, legal_entity_id: UUID) -> dict[tuple[str, int], TaxInstalmentPlan]:
        """Every plan of the legal entity, by tax and tax year."""
        return {
            (row.tax, row.tax_year): row
            for row in self.session.execute(
                select(TaxInstalmentPlan).where(
                    TaxInstalmentPlan.legal_entity_id == legal_entity_id
                )
            ).scalars()
        }

    def save(
        self, legal_entity_id: UUID, tax: str, tax_year: int, instalments: int
    ) -> TaxInstalmentPlan:
        """One row per legal entity, tax and year, replaced by the next choice. An upsert, so two
        administrators choosing at once both land and the second one stands."""
        row_id = self.session.execute(
            pg_insert(TaxInstalmentPlan)
            .values(
                legal_entity_id=legal_entity_id,
                tax=tax,
                tax_year=tax_year,
                instalments=instalments,
            )
            .on_conflict_do_update(
                constraint="uq_tax_instalment_plans",
                set_={"instalments": instalments, "updated_at": func.now()},
            )
            .returning(TaxInstalmentPlan.id)
        ).scalar_one()
        row = self.session.get(TaxInstalmentPlan, row_id)
        assert row is not None
        self.session.refresh(row)
        return row

    def delete(self, legal_entity_id: UUID, tax: str, tax_year: int) -> TaxInstalmentPlan | None:
        """Removes the plan and answers it, `None` when there was none."""
        row = self.for_entity(legal_entity_id).get((tax, tax_year))
        if row is not None:
            self.session.delete(row)
            self.session.flush()
        return row


class TaxNoticePlanRepository:
    """The notice plans of `tax_notice_plans`. Never commits."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, plan_id: UUID) -> TaxNoticePlan | None:
        return self.session.get(TaxNoticePlan, plan_id)

    def for_entity(self, legal_entity_id: UUID) -> list[TaxNoticePlan]:
        return list(
            self.session.execute(
                select(TaxNoticePlan)
                .where(TaxNoticePlan.legal_entity_id == legal_entity_id)
                .order_by(TaxNoticePlan.created_at, TaxNoticePlan.id)
            ).scalars()
        )

    def add(self, row: TaxNoticePlan) -> TaxNoticePlan:
        self.session.add(row)
        self.session.flush()
        return row

    def delete(self, row: TaxNoticePlan) -> None:
        self.session.delete(row)
        self.session.flush()
