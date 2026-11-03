from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contracts.cap_service import Addition, ContractCapService
from pigrocrm.core.contracts.conversion_service import ContractConversionService
from pigrocrm.core.contracts.models import ClauseNote, Contract
from pigrocrm.core.contracts.renewal_service import ContractRenewalService
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.contracts.rules import (
    ENTITY,
    check_payment_terms_together,
    check_renewal_notice,
)
from pigrocrm.core.contracts.schemas import (
    CONTRACT_SORTS,
    ClauseNoteRead,
    ContractCapSimulation,
    ContractConcentrationCap,
    ContractCreate,
    ContractDetail,
    ContractFromDeal,
    ContractFromDealProposal,
    ContractListQuery,
    ContractPage,
    ContractRead,
    ContractRenew,
    ContractStateChange,
    ContractUpdate,
)
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.db import encode_cursor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.labels import CONTRACT_STATUS, label
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# How many clause notes `ContractService.detail` carries, the latest ones.
DETAIL_NOTES_LIMIT = 50


class ContractService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ContractRepository(session)
        self.customers = CustomerRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)
        self.legal_entity = LegalEntityService(session)

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        """Used by `create` only: `values` is the *complete* desired set of custom
        fields for a brand-new row, so it is validated against every active
        definition. Mirrors CustomerService._validated_custom/DealService.
        _validated_custom exactly."""
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def create(self, data: ContractCreate, actor: Actor) -> ContractRead:
        actor.require_write("create_contract")
        payload = data.model_dump()
        check_payment_terms_together(payload)
        check_renewal_notice(payload)

        customer = self.customers.get(payload["customer_id"])
        if customer is None:
            raise NotFound("customer", payload["customer_id"])
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})
        # The customer's legal entity at this moment, kept from now on (REB-623, spec §1.7);
        # refused when deactivated.
        payload["legal_entity_id"] = self.legal_entity.inherited(customer.legal_entity_id, ENTITY)

        contract = self.repo.add(Contract(**payload))
        self.activities.record(ENTITY, contract.id, "created", actor, {"title": contract.title})
        self.session.commit()
        return ContractRead.model_validate(contract)

    def get(self, contract_id: UUID, actor: Actor) -> ContractRead:
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        return ContractRead.model_validate(contract)

    def detail(self, contract_id: UUID, actor: Actor) -> ContractDetail:
        """`get`, with what hangs off the one contract: its clause notes, oldest first, and
        the contract that renewed it. What the contract page opens and `get_contract`
        answers, so an agent reads the person's decisions about the clauses with the
        terms they are about."""
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        # The latest `DETAIL_NOTES_LIMIT`, oldest first: each note can run to pages, and a
        # contract that holds more is read in full through the notes' own list.
        latest = self.session.scalars(
            select(ClauseNote)
            .where(ClauseNote.contract_id == contract_id)
            .order_by(ClauseNote.created_at.desc(), ClauseNote.id.desc())
            .limit(DETAIL_NOTES_LIMIT)
        )
        notes = reversed(list(latest))
        total = self.session.scalar(
            select(func.count())
            .select_from(ClauseNote)
            .where(ClauseNote.contract_id == contract_id)
        )
        return ContractDetail(
            **ContractRead.model_validate(contract).model_dump(),
            next_contract_id=self.session.scalar(
                select(Contract.id).where(
                    Contract.previous_contract_id == contract_id,
                    Contract.deleted_at.is_(None),
                )
            ),
            note_clauses=[ClauseNoteRead.model_validate(note) for note in notes],
            note_total_clauses=total or 0,
        )

    def update(self, contract_id: UUID, data: ContractUpdate, actor: Actor) -> ContractRead:
        """A partial patch. The cross-field rules run on the merged row, so a call that
        moves `renewal_type` to `tacit` without a notice is refused, and so is one that
        clears `payment_days` and leaves `payment_end_of_month` behind."""
        actor.require_write("update_contract")
        contract = self.repo.get_locked(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)

        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Contract, changes)
        merged = {
            key: changes.get(key, getattr(contract, key))
            for key in (
                "start",
                "end",
                "renewal_type",
                "renewal_notice_days",
                "payment_days",
                "payment_end_of_month",
            )
        }
        check_payment_terms_together(merged)
        check_renewal_notice(merged)
        if merged["end"] is not None and merged["end"] < merged["start"]:
            raise ValidationFailed(
                ENTITY,
                "end",
                "non può precedere inizio",
                expected=f">= {merged['start'].isoformat()}",
            )
        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY, self.fields.specs_for(ENTITY), contract.custom_fields, data.custom_fields
            )

        if not changes:
            # Nothing moved: no write, and no «updated» row in the timeline for it.
            return ContractRead.model_validate(contract)

        for key, value in changes.items():
            setattr(contract, key, value)
        self.session.flush()
        if "expense_policy" in changes:
            self._reevaluate_unbilled_expenses(contract.id)
        self.activities.record(ENTITY, contract.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return ContractRead.model_validate(contract)

    def _reevaluate_unbilled_expenses(self, contract_id: UUID) -> None:
        """`contract_expenses.reimbursable` is written by a trigger when an expense is
        inserted or updated, from the contract's policy as it stands then. A policy
        that changes afterwards would leave every existing expense with the verdict of
        the old one, still «Da fatturare» and still picked up by the invoice assembly,
        so the trigger is fired again, with a no-op update, on the contract's expenses
        that are not on an invoice yet. One already on an invoice keeps the verdict it
        was billed under."""
        # `RETURNING` hands back each re-fired row as the trigger wrote it, and
        # `populate_existing` overwrites the copy of it already loaded in this session:
        # only these rows are refreshed, the caller's other loaded objects and pending
        # changes are left alone.
        self.session.execute(
            update(ContractExpense)
            .where(
                ContractExpense.contract_id == contract_id,
                ContractExpense.invoice_line_id.is_(None),
            )
            .values(pre_authorized=ContractExpense.pre_authorized)
            .returning(ContractExpense)
            .execution_options(populate_existing=True, synchronize_session=False)
        ).all()

    def set_state(self, contract_id: UUID, data: ContractStateChange, actor: Actor) -> ContractRead:
        """The one writer of `Contract.status`. Any state to any other; the same state
        again is refused, so a double click never writes a second timeline row."""
        actor.require_write("set_contract_state")
        contract = self.repo.get_locked(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        if contract.status == data.status:
            raise Conflict(
                ENTITY,
                f"il contratto è già «{label(CONTRACT_STATUS, data.status)}»",
                contract_id=str(contract_id),
            )
        previous = contract.status
        contract.status = data.status
        self.session.flush()
        self.activities.record(
            ENTITY,
            contract.id,
            "state_changed",
            actor,
            {"from": previous, "to": data.status},
        )
        self.session.commit()
        return ContractRead.model_validate(contract)

    def renew(self, contract_id: UUID, data: ContractRenew, actor: Actor) -> ContractRead:
        """The contract that renews `contract_id`. See `ContractRenewalService.renew`."""
        return ContractRenewalService(self.session).renew(contract_id, data, actor)

    def proposal_from_deal(
        self, deal_id: UUID, actor: Actor, start: date | None = None
    ) -> ContractFromDealProposal:
        """What converting a won deal would start from. See
        `ContractConversionService.propose`."""
        return ContractConversionService(self.session).propose(deal_id, actor, start)

    def create_from_deal(self, deal_id: UUID, data: ContractFromDeal, actor: Actor) -> ContractRead:
        """The contract a won deal becomes. See `ContractConversionService.create`."""
        return ContractConversionService(self.session).create(deal_id, data, actor)

    def concentration_cap(
        self,
        contract_id: UUID,
        actor: Actor,
        as_of: date | None = None,
        threshold: float | None = None,
    ) -> ContractConcentrationCap:
        """One engagement's share of revenue over its anniversary year. See
        `ContractCapService.concentration_cap`."""
        return ContractCapService(self.session).concentration_cap(
            contract_id, actor, as_of, threshold
        )

    def simulate_cap(
        self, contract_id: UUID, addition: Addition, expected_date: date | None, actor: Actor
    ) -> ContractCapSimulation:
        """The contract's cap against its customer's share after a synthetic addition. See
        `ContractCapService.simulate_cap`."""
        return ContractCapService(self.session).simulate_cap(
            contract_id, addition, expected_date, actor
        )

    # `list` is defined LAST in this class on purpose -- see ContractRepository.list's
    # own comment for the Python 3.13 annotation-evaluation reason.
    def list(self, query: ContractListQuery, actor: Actor) -> ContractPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        spec = CONTRACT_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return ContractPage(
            items=[ContractRead.model_validate(c) for c in items],
            next_cursor=next_cursor,
        )
