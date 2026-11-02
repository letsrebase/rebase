from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contracts.cap_service import ContractCapService
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
    ContractListQuery,
    ContractPage,
    ContractRead,
    ContractRenew,
    ContractStateChange,
    ContractUpdate,
)
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.db import encode_cursor
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
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
        self.emitter = LegalEntityService(session)

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
        # The customer's azienda at this moment, kept from now on (REB-623, spec §1.7);
        # refused when deactivated.
        payload["azienda_id"] = self.emitter.inherited(customer.azienda_id, ENTITY)

        contract = self.repo.add(Contract(**payload))
        self.activities.record(ENTITY, contract.id, "created", actor, {"titolo": contract.titolo})
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
        totale = self.session.scalar(
            select(func.count())
            .select_from(ClauseNote)
            .where(ClauseNote.contract_id == contract_id)
        )
        return ContractDetail(
            **ContractRead.model_validate(contract).model_dump(),
            contratto_successivo_id=self.session.scalar(
                select(Contract.id).where(
                    Contract.contratto_precedente_id == contract_id,
                    Contract.deleted_at.is_(None),
                )
            ),
            note_clausole=[ClauseNoteRead.model_validate(note) for note in notes],
            note_clausole_totale=totale or 0,
        )

    def update(self, contract_id: UUID, data: ContractUpdate, actor: Actor) -> ContractRead:
        """A partial patch. The cross-field rules run on the merged row, so a call that
        moves `tipo_rinnovo` to `tacito` without a notice is refused, and so is one that
        clears `giorni_pagamento` and leaves `pagamento_fine_mese` behind."""
        actor.require_write("update_contract")
        contract = self.repo.get_locked(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)

        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Contract, changes)
        merged = {
            key: changes.get(key, getattr(contract, key))
            for key in (
                "inizio",
                "fine",
                "tipo_rinnovo",
                "preavviso_rinnovo_giorni",
                "giorni_pagamento",
                "pagamento_fine_mese",
            )
        }
        check_payment_terms_together(merged)
        check_renewal_notice(merged)
        if merged["fine"] is not None and merged["fine"] < merged["inizio"]:
            raise ValidationFailed(
                ENTITY,
                "fine",
                "non può precedere inizio",
                expected=f">= {merged['inizio'].isoformat()}",
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
        if "politica_spese" in changes:
            self._reevaluate_unbilled_expenses(contract.id)
        self.activities.record(ENTITY, contract.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return ContractRead.model_validate(contract)

    def _reevaluate_unbilled_expenses(self, contract_id: UUID) -> None:
        """`contract_expenses.rimborsabile` is written by a trigger when an expense is
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
            .values(pre_autorizzata=ContractExpense.pre_autorizzata)
            .returning(ContractExpense)
            .execution_options(populate_existing=True, synchronize_session=False)
        ).all()

    def set_state(self, contract_id: UUID, data: ContractStateChange, actor: Actor) -> ContractRead:
        """The one writer of `Contract.stato`. Any state to any other; the same state
        again is refused, so a double click never writes a second timeline row."""
        actor.require_write("set_contract_state")
        contract = self.repo.get_locked(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        if contract.stato == data.stato:
            raise Conflict(
                ENTITY,
                f"il contratto è già «{data.stato}»",
                contract_id=str(contract_id),
            )
        previous = contract.stato
        contract.stato = data.stato
        self.session.flush()
        self.activities.record(
            ENTITY,
            contract.id,
            "state_changed",
            actor,
            {"from": previous, "to": data.stato},
        )
        self.session.commit()
        return ContractRead.model_validate(contract)

    def renew(self, contract_id: UUID, data: ContractRenew, actor: Actor) -> ContractRead:
        """The contract that renews `contract_id`. See `ContractRenewalService.renew`."""
        return ContractRenewalService(self.session).renew(contract_id, data, actor)

    def concentration_cap(
        self,
        contract_id: UUID,
        actor: Actor,
        as_of: date | None = None,
        soglia: float | None = None,
    ) -> ContractConcentrationCap:
        """One engagement's share of revenue over its anniversary year. See
        `ContractCapService.concentration_cap`."""
        return ContractCapService(self.session).concentration_cap(contract_id, actor, as_of, soglia)

    def simulate_cap(
        self, contract_id: UUID, aggiunta: Decimal, data_prevista: date | None, actor: Actor
    ) -> ContractCapSimulation:
        """The contract's cap against its customer's share after a synthetic addition. See
        `ContractCapService.simulate_cap`."""
        return ContractCapService(self.session).simulate_cap(
            contract_id, aggiunta, data_prevista, actor
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
