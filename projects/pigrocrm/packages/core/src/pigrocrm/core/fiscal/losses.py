"""The only writer of `tax_losses` (REB-1088, taxes design § 1 and § 5).

A tax loss is what the accountant's return says a period closed with, typed by an administrator
and carried to the years after it by the estimate (`fiscal/loss_carry.py`). It is the same kind of
fact as a declared figure and has the same position: an administrator whose scope covers the legal
entity, an agent only with `full_access` (`agent_actions.py`). Typing or removing one moves the
estimate of the years after it, so the legal entity's tax due items are recomputed in the same
transaction (`TaxDueItemService`), exactly as typing a declared figure does.

Method names are unique across the services on purpose: `FORBIDDEN_SERVICE_CALLS` of the MCP ban
test finds the privileged tools by name.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.loss_schemas import LossTax, TaxLossRead, TaxLossSet
from pigrocrm.core.fiscal.pack import resolve_pack
from pigrocrm.core.fiscal.repository import FiscalProfileRepository, TaxLossRepository
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.legal_entities.service import LegalEntityService

ENTITY = "tax_loss"

_OWES = {"ires": "IRES", "irpef": "IRPEF"}


class TaxLossService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TaxLossRepository(session)
        self.activities = ActivityService(session)

    def set_tax_loss(
        self,
        data: TaxLossSet,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
    ) -> TaxLossRead:
        """Types the loss of `data.year` for `data.tax` and replaces any typed before. One legal
        entity's, always: it resolves to the only one when none is named and is required, by
        name, when the space has several. The tax has to be one the legal entity's pack carries
        a loss against (IRES for an srl, IRPEF for an ordinary person)."""
        actor.require_admin("set_tax_loss")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        self._require_carried(legal_entity.id, data.tax)
        row = self.repo.save(
            legal_entity.id,
            data.tax,
            data.year,
            amount=data.amount,
            remaining=data.remaining if data.remaining is not None else data.amount,
            remaining_as_of_year=(
                data.remaining_as_of_year if data.remaining_as_of_year is not None else data.year
            ),
            new_activity=data.new_activity,
            activity_start_year=data.activity_start_year,
            source="typed",
        )
        # The estimates of the years after the loss move with it, and so do the items they feed.
        TaxDueItemService(self.session).refresh_tax_due_items(legal_entity.id)
        # Who typed it and when, for which tax and year; never the amount, as the declared
        # figures' timeline entries never carry one.
        self.activities.record(
            ENTITY, row.id, "tax_loss_set", actor, {"tax": data.tax, "year": data.year}
        )
        self.session.commit()
        self.session.refresh(row)
        return TaxLossRead.model_validate(row)

    def delete_tax_loss(
        self,
        tax: LossTax,
        year: int,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
    ) -> None:
        """Removes the loss of `year` for `tax`: a loss typed by mistake, or one the return does
        not carry. The estimate then reads the year as it would have with none typed."""
        actor.require_admin("delete_tax_loss")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        row = self.repo.delete(legal_entity.id, tax, year)
        if row is None:
            raise NotFound(ENTITY, f"{tax}:{year}")
        TaxDueItemService(self.session).refresh_tax_due_items(legal_entity.id)
        self.activities.record(
            ENTITY, row.id, "tax_loss_deleted", actor, {"tax": tax, "year": year}
        )
        self.session.commit()

    def losses_for_entity(self, legal_entity_id: UUID) -> list[TaxLossRead]:
        """Every loss typed for one legal entity, oldest year first. No role check of its own: its
        callers (`get_fiscal_estimate`) are administrators' already."""
        return [TaxLossRead.model_validate(row) for row in self.repo.for_entity(legal_entity_id)]

    def _require_carried(self, legal_entity_id: UUID, tax: LossTax) -> None:
        profile = FiscalProfileRepository(self.session).get(legal_entity_id)
        if profile is None:
            # As `get_fiscal_estimate` answers: nothing is computed without a profile.
            raise NotFound("fiscal_profile", legal_entity_id)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        carried = any(
            (law.company if tax == "ires" else law.person_business) is not None
            for law in pack.loss_carry
        )
        if not carried:
            raise ValidationFailed(
                ENTITY,
                "tax",
                f"questa azienda non è soggetta all'{_OWES[tax]}: la perdita non si riporta",
            )


__all__ = ["ENTITY", "TaxLossService"]
