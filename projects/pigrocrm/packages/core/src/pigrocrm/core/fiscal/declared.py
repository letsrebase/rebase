"""The only writer of `declared_tax_figures` (REB-1006, taxes design § 2 and § 5).

A declared figure is what the accountant's tax return says, typed by an administrator. It is
never computed here, never replaced by an estimate, and it outranks the estimate of the same
tax and year (`reading_for_year`): `AnalyticsService.get_fiscal_estimate` shows it beside the
estimate and says the year's reading is declared. The service asks for an administrator first,
before it looks anything up; an agent credential passes only with `full_access`
(`agent_actions.py`). Which legal entities an administrator reaches is the row policy's
business (`legal_entity_scope`).

Method names are unique across the services on purpose: `FORBIDDEN_SERVICE_CALLS` of the MCP
ban test finds the privileged tools by name.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.fiscal.models import DeclaredTaxFigure
from pigrocrm.core.fiscal.repository import DeclaredTaxFigureRepository
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureRead, DeclaredTaxFigureSet
from pigrocrm.core.legal_entities.service import LegalEntityService

ENTITY = "declared_tax_figure"


def reading_for_year(declared: list[DeclaredTaxFigureRead]) -> Literal["estimate", "declared"]:
    """The reading of a year: the stages rank, never the time (taxes design § 2), so one
    declared figure makes the year declared whatever estimate was computed after it. «Paid»
    outranks both and has no source yet."""
    return "declared" if declared else "estimate"


class DeclaredTaxFigureService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = DeclaredTaxFigureRepository(session)
        self.activities = ActivityService(session)

    def set_declared_tax_figure(
        self,
        data: DeclaredTaxFigureSet,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
    ) -> DeclaredTaxFigureRead:
        """Types the figures of `data.tax` for `data.year` and replaces any typed before.
        One legal entity's, always: it resolves to the only one when none is named and is
        required, by name, when the space has several."""
        actor.require_admin("set_declared_tax_figure")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        row = self.repo.save(
            legal_entity.id,
            data.tax,
            data.year,
            tax_due=data.tax_due,
            balance=data.balance,
            advances=data.advances,
            source="typed",
        )
        # Who typed it and when; never the amounts, which are the most sensitive figures the
        # product holds and stay out of the timeline.
        self.activities.record(ENTITY, row.id, "declared_tax_figure_set", actor)
        self.session.commit()
        self.session.refresh(row)
        return DeclaredTaxFigureRead.model_validate(row)

    def declared_for_year(self, legal_entity_id: UUID, year: int) -> list[DeclaredTaxFigureRead]:
        """The figures declared for one legal entity's year. No role check of its own: its
        callers (`get_fiscal_estimate`) are administrators' already."""
        rows: list[DeclaredTaxFigure] = self.repo.for_year(legal_entity_id, year)
        return [DeclaredTaxFigureRead.model_validate(row) for row in rows]
