"""The only writer of `tax_advance_methods` (REB-1074, taxes design § 2 and § 5).

An administrator chooses, for a legal entity and a tax year, how the advances of that year are
computed: `historical` (the default; the previous year's declared tax, or its estimate when none
is declared) or `forecast` (the current year's estimate). The choice moves the tax due items, so
it is the same act as typing a declared figure: an administrator whose scope covers the legal
entity, an agent only with `full_access` (`agent_actions.py`). The service asks for an
administrator first, before it looks anything up, then recomputes the legal entity's tax due
items (`TaxDueItemService`) in the same transaction and answers what it could not write and why.

Method names are unique across the services on purpose: `FORBIDDEN_SERVICE_CALLS` of the MCP ban
test finds the privileged tools by name.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.advance_schemas import (
    AdvanceMethod,
    TaxAdvanceMethodRead,
    TaxAdvanceMethodSet,
)
from pigrocrm.core.fiscal.repository import FiscalProfileRepository, TaxAdvanceMethodRepository
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.legal_entities.service import LegalEntityService

ENTITY = "tax_advance_method"


class TaxAdvanceMethodService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TaxAdvanceMethodRepository(session)
        self.activities = ActivityService(session)

    def set_tax_advance_method(
        self,
        data: TaxAdvanceMethodSet,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
    ) -> TaxAdvanceMethodRead:
        """Chooses the method of `data.year`'s advances, replacing a choice made before, and
        recomputes the legal entity's tax due items. One legal entity's, always: it resolves to
        the only one when none is named and is required, by name, when the space has several."""
        actor.require_admin("set_tax_advance_method")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        if FiscalProfileRepository(self.session).get(legal_entity.id) is None:
            # As `get_fiscal_estimate` answers: nothing is computed without a profile.
            raise NotFound("fiscal_profile", legal_entity.id)
        row = self.repo.save(legal_entity.id, data.year, data.method)
        report = TaxDueItemService(self.session).refresh_tax_due_items(legal_entity.id)
        # Who chose and when, and for which year; never an amount, which stays out of the
        # timeline as the declared figures' do.
        self.activities.record(ENTITY, row.id, "tax_advance_method_set", actor, {"year": data.year})
        self.session.commit()
        self.session.refresh(row)
        method: AdvanceMethod = "forecast" if row.method == "forecast" else "historical"
        return TaxAdvanceMethodRead(
            legal_entity_id=legal_entity.id,
            year=row.tax_year,
            method=method,
            updated_at=row.updated_at,
            items=report,
        )
