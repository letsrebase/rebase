from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contracts import projection
from pigrocrm.core.contracts.dates import anniversary_year_bounds
from pigrocrm.core.contracts.models import Contract, RateCard, RenewalAssumption
from pigrocrm.core.contracts.repository import (
    ContractRepository,
    RateCardRepository,
    RenewalAssumptionRepository,
)
from pigrocrm.core.contracts.schemas import (
    CONTRACT_SORTS,
    ConcentrationFigures,
    ContractConcentrationCap,
    ContractCreate,
    ContractListQuery,
    ContractPage,
    ContractProjectionQuery,
    ContractProjectionRead,
    ContractRead,
    ContractStateChange,
    ContractUpdate,
    RateCardCreate,
    RateCardRead,
    RateCardUpdate,
    RenewalAssumptionRead,
    RenewalAssumptionUpsert,
)
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.db import encode_cursor, today_local
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`), matching
# CustomerService.ENTITY/DealService.ENTITY exactly -- see those classes for why a
# plain `str` would fail mypy strict at `specs_for`'s own call site.
ENTITY: EntityType = "contract"


def check_payment_terms_together(payload: dict[str, Any]) -> None:
    """Mirrors `ck_contracts_payment_terms_together`: a contract states both of its
    own payment-term facts or neither, never one paired with a term it never set."""
    if (payload.get("giorni_pagamento") is None) != (payload.get("pagamento_fine_mese") is None):
        raise ValidationFailed(
            ENTITY,
            "giorni_pagamento",
            "giorni_pagamento e pagamento_fine_mese vanno impostati insieme, o nessuno dei due",
            expected="entrambi valorizzati oppure entrambi assenti",
        )


def check_renewal_notice(payload: dict[str, Any]) -> None:
    """Mirrors `ck_contracts_preavviso_rinnovo_required`: applicable, and required,
    for every renewal type except 'nessuno'."""
    if payload["tipo_rinnovo"] != "nessuno" and payload.get("preavviso_rinnovo_giorni") is None:
        raise ValidationFailed(
            ENTITY,
            "preavviso_rinnovo_giorni",
            "obbligatorio per ogni tipo di rinnovo diverso da 'nessuno'",
            expected="un numero di giorni",
        )


class ContractService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ContractRepository(session)
        self.customers = CustomerRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)
        self.analytics = AnalyticsRepository(session)

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        """Used by `create` only: `values` is the *complete* desired set of custom
        fields for a brand-new row, so it is validated against every active
        definition. Mirrors CustomerService._validated_custom/DealService.
        _validated_custom exactly."""
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _update_custom_fields(self, contract: Contract, provided: dict[str, Any]) -> dict[str, Any]:
        """`DealService._update_custom_fields`'s contract (see its docstring for the
        reasoning): only the keys the caller touches are validated, against the active
        definitions; a key supplied as `None` is removed unless its definition is
        required and active; the keys not mentioned are carried over untouched."""
        active_by_key = {spec.key: spec for spec in self.fields.specs_for(ENTITY)}

        to_remove: set[str] = set()
        for key, value in provided.items():
            if value is not None:
                continue
            spec = active_by_key.get(key)
            if spec is not None and spec.required:
                raise ValidationFailed(
                    ENTITY, key, "campo obbligatorio", expected="un valore non vuoto"
                )
            to_remove.add(key)

        to_set = {key: value for key, value in provided.items() if value is not None}
        touched_specs = [spec for spec in active_by_key.values() if spec.key in to_set]
        validated = validate_custom_fields(ENTITY, touched_specs, to_set)

        merged = {k: v for k, v in contract.custom_fields.items() if k not in to_remove}
        merged.update(validated)
        return merged

    def create(self, data: ContractCreate, actor: Actor) -> ContractRead:
        actor.require_write("create_contract")
        payload = data.model_dump()
        check_payment_terms_together(payload)
        check_renewal_notice(payload)

        customer = self.customers.get(payload["customer_id"])
        if customer is None:
            raise NotFound("customer", payload["customer_id"])
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})
        # The customer's azienda at this moment, kept from now on (REB-623, spec §1.7).
        # The customer's azienda at this moment, kept from now on (REB-623, spec §1.7);
        # refused when deactivated.
        payload["azienda_id"] = LegalEntityService(self.session).inherited(
            customer.azienda_id, ENTITY
        )

        contract = self.repo.add(Contract(**payload))
        self.activities.record(ENTITY, contract.id, "created", actor, {"titolo": contract.titolo})
        self.session.commit()
        return ContractRead.model_validate(contract)

    def get(self, contract_id: UUID, actor: Actor) -> ContractRead:
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        return ContractRead.model_validate(contract)

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
            changes["custom_fields"] = self._update_custom_fields(contract, data.custom_fields)

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
        self.session.execute(
            update(ContractExpense)
            .where(
                ContractExpense.contract_id == contract_id,
                ContractExpense.invoice_line_id.is_(None),
            )
            .values(pre_autorizzata=ContractExpense.pre_autorizzata)
            .execution_options(synchronize_session=False)
        )
        # The trigger wrote `rimborsabile` behind the ORM's back: anything already
        # loaded in this session is stale.
        self.session.expire_all()

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

    def concentration_cap(
        self,
        contract_id: UUID,
        actor: Actor,
        as_of: date | None = None,
        soglia: float | None = None,
    ) -> ContractConcentrationCap:
        """REB-352 §1.5: one engagement's own share of total invoiced revenue over
        the anniversary year containing `as_of` (today, in the emitter's own zone,
        when not given). Read-only, like every other figure `AnalyticsService`
        exposes -- no `require_write`. `soglia` is a caller-supplied share in [0, 1];
        `superata` stays `None` until one is given, the same "no persistence
        required" reading REB-352 §5 item 4 gives the ceiling simulator.
        """
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        reference = as_of if as_of is not None else today_local()
        if reference < contract.inizio:
            raise ValidationFailed(
                ENTITY,
                "as_of",
                "precede l'inizio del contratto",
                expected=f">= {contract.inizio.isoformat()}",
            )
        periodo_da, periodo_a = anniversary_year_bounds(contract.inizio, reference)
        # Within the contract's own azienda (REB-630, spec 2026-10-03 §1.9): a share of
        # revenue is a share of one azienda's revenue, and the contract names which.
        # Per currency, never converted (REB-776): the contract's own `divisa` says what
        # its fees are in, not what the customer's invoices were issued in.
        revenue = self.analytics.revenue_for_customer_in_window(
            contract.customer_id, periodo_da, periodo_a, azienda_id=contract.azienda_id
        )
        if not revenue:
            revenue = [(contract.divisa, Decimal("0.00"), Decimal("0.00"))]
        valute = []
        for divisa, ricavi_cliente, ricavi_totali in revenue:
            quota = float(ricavi_cliente / ricavi_totali) if ricavi_totali > 0 else 0.0
            valute.append(
                ConcentrationFigures(
                    divisa=divisa,
                    ricavi_cliente=ricavi_cliente,
                    ricavi_totali=ricavi_totali,
                    quota=quota,
                    superata=(quota >= soglia) if soglia is not None else None,
                )
            )
        return ContractConcentrationCap(
            contract_id=contract.id,
            customer_id=contract.customer_id,
            periodo_da=periodo_da,
            periodo_a=periodo_a,
            soglia=soglia,
            valute=valute,
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


def check_rate_card_shape(payload: dict[str, Any]) -> None:
    """Mirrors the two conditional CHECKs on `rate_cards`: `ore_minime` only for
    `tipo = 'orario'`, `periodo_erogazione` only for `tipo = 'ricorrente_fisso'`."""
    if payload["tipo"] != "orario" and payload.get("ore_minime") is not None:
        raise ValidationFailed(
            "rate_card",
            "ore_minime",
            "significativo solo per una scheda di tipo 'orario'",
            expected="assente per ogni altro tipo",
        )
    if payload["tipo"] != "ricorrente_fisso" and payload.get("periodo_erogazione") is not None:
        raise ValidationFailed(
            "rate_card",
            "periodo_erogazione",
            "significativo solo per una scheda di tipo 'ricorrente_fisso'",
            expected="assente per ogni altro tipo",
        )


class RateCardService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = RateCardRepository(session)
        self.contracts = ContractRepository(session)
        self.activities = ActivityService(session)

    def create(
        self,
        contract_id: UUID,
        data: RateCardCreate,
        actor: Actor,
        *,
        close_previous: bool = False,
    ) -> RateCardRead:
        """`close_previous` asks for the card in force on `data.valido_da` to be closed
        the day before it, in the same transaction, instead of the overlap being
        refused. It is an argument of the call and not a field of `RateCardCreate`,
        because that schema is also what a proposal carries for its first card, which
        has nothing to replace. Off by default: an overlap stays refused unless the
        caller said it meant to replace the current rate."""
        actor.require_write("create_rate_card")
        contract = self.contracts.get(contract_id)
        if contract is None:
            raise NotFound("contract", contract_id)

        payload = data.model_dump()
        check_rate_card_shape(payload)
        payload["contract_id"] = contract_id

        rate_card = RateCard(**payload)
        closed: RateCard | None = None
        try:
            if close_previous:
                closed = self._close_card_in_force(contract_id, rate_card.valido_da)
            self.session.add(rate_card)
            self.session.flush()
        except IntegrityError as exc:
            # ck_rate_cards_no_overlap is the real authority here (Done-when: "refused
            # by the database, not by a service-level check") -- this only turns the
            # raw exclusion violation into this project's own clean error, the same
            # way LegalEntityService.upsert/FiscalProfileService.upsert already do
            # for their own singleton race.
            self.session.rollback()
            raise Conflict(
                "rate_card",
                "il periodo di validità si sovrappone a un'altra scheda tariffaria "
                "di questo contratto",
                contract_id=str(contract_id),
            ) from exc

        self.activities.record(
            "contract",
            contract_id,
            "rate_card_added",
            actor,
            {
                "tipo": rate_card.tipo,
                "valido_da": str(rate_card.valido_da),
                # Why another card's end moved, when it did.
                **(
                    {
                        "precedente_valido_da": str(closed.valido_da),
                        "precedente_valido_a": str(closed.valido_a),
                    }
                    if closed is not None
                    else {}
                ),
            },
        )
        self.session.commit()
        return RateCardRead.model_validate(rate_card)

    def _close_card_in_force(self, contract_id: UUID, start: date) -> RateCard | None:
        """Ends, the day before `start`, the card that began earlier and is still in
        force on `start` -- the one a new rate from `start` replaces. The exclusion
        constraint allows at most one such card per contract. A card that began on or
        after `start` is not touched, so a genuine overlap is refused as ever. Returns
        the card it closed, so the timeline can say why its end moved."""
        for card in self.repo.list_for_contract(contract_id):
            if card.valido_da < start and (card.valido_a is None or card.valido_a >= start):
                card.valido_a = start - timedelta(days=1)
                self.session.flush()
                return card
        return None

    def update(
        self, contract_id: UUID, rate_card_id: UUID, data: RateCardUpdate, actor: Actor
    ) -> RateCardRead:
        actor.require_write("update_rate_card")
        card = self.repo.get(rate_card_id)
        if card is None or card.contract_id != contract_id:
            raise NotFound("rate_card", rate_card_id)

        changes = supplied_changes(data)
        reject_cleared_columns("rate_card", RateCard, changes)
        merged = {
            key: changes.get(key, getattr(card, key))
            for key in ("valido_da", "valido_a", "tipo", "ore_minime", "periodo_erogazione")
        }
        check_rate_card_shape(merged)
        if merged["valido_a"] is not None and merged["valido_a"] < merged["valido_da"]:
            raise ValidationFailed(
                "rate_card",
                "valido_a",
                "non può precedere valido_da",
                expected=f">= {merged['valido_da'].isoformat()}",
            )

        for key, value in changes.items():
            setattr(card, key, value)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                "rate_card",
                "il periodo di validità si sovrappone a un'altra scheda tariffaria "
                "di questo contratto",
                contract_id=str(contract_id),
            ) from exc

        self.activities.record(
            "contract",
            contract_id,
            "rate_card_updated",
            actor,
            {"changed": sorted(changes), "valido_da": str(card.valido_da)},
        )
        self.session.commit()
        return RateCardRead.model_validate(card)

    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[RateCardRead]:
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)
        return [
            RateCardRead.model_validate(card) for card in self.repo.list_for_contract(contract_id)
        ]


class RenewalAssumptionService:
    """A contract's own recorded belief about revenue beyond its known term
    (REB-375). One row per contract, revised in place -- see `RenewalAssumption`
    (contracts/models.py) for why this is a singleton-per-parent, not a history."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = RenewalAssumptionRepository(session)
        self.contracts = ContractRepository(session)
        self.activities = ActivityService(session)

    def upsert(
        self, contract_id: UUID, data: RenewalAssumptionUpsert, actor: Actor
    ) -> RenewalAssumptionRead:
        """Create-or-update the one row for `contract_id`.

        `repo.add` sits **inside** the `try`, not before it: it is the only
        statement that can violate `uq_renewal_assumptions_contract_id`, and leaving
        it outside would let two concurrent first-time saves poison the session with
        a raw `IntegrityError` instead of surfacing a clean `Conflict` -- the same
        shape `FiscalProfileService.upsert` already uses for its own singleton race.
        """
        actor.require_write("set_renewal_assumption")
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)

        payload = data.model_dump()
        assumption = self.repo.get_for_contract(contract_id)
        try:
            if assumption is None:
                assumption = self.repo.add(RenewalAssumption(contract_id=contract_id, **payload))
            else:
                for key, value in payload.items():
                    setattr(assumption, key, value)
                self.session.flush()
            self.activities.record(
                "contract",
                contract_id,
                "renewal_assumption_set",
                actor,
                {
                    "probabilita": assumption.probabilita,
                    "orizzonte_al": str(assumption.orizzonte_al),
                },
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                "renewal_assumption",
                "esiste già un'assunzione di rinnovo per questo contratto",
                contract_id=str(contract_id),
            ) from exc
        return RenewalAssumptionRead.model_validate(assumption)

    def get(self, contract_id: UUID, actor: Actor) -> RenewalAssumptionRead:
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)
        assumption = self.repo.get_for_contract(contract_id)
        if assumption is None:
            raise NotFound("renewal_assumption", contract_id)
        return RenewalAssumptionRead.model_validate(assumption)


class ContractProjectionService:
    """The genuine "projected" figure a contract's own recurring-fee schedule and
    renewal assumption produce (REB-375) -- entirely apart from
    `AnalyticsService.cash_overview`'s draft-based `proiettato`. Reads only: every
    number here is derived, nothing is written."""

    def __init__(self, session: Session) -> None:
        self.contracts = ContractRepository(session)
        self.rate_cards = RateCardRepository(session)
        self.renewal_assumptions = RenewalAssumptionRepository(session)

    def project(
        self, contract_id: UUID, query: ContractProjectionQuery, actor: Actor
    ) -> ContractProjectionRead:
        contract = self.contracts.get(contract_id)
        if contract is None:
            raise NotFound("contract", contract_id)

        come_di: date = query.come_di if query.come_di is not None else today_local()
        rate_cards = self.rate_cards.list_for_contract(contract_id)
        assumption = self.renewal_assumptions.get_for_contract(contract_id)
        result = projection.project(contract, rate_cards, assumption, come_di, query.da, query.a)
        return ContractProjectionRead(
            contract_id=result.contract_id,
            come_di=result.come_di,
            da=result.da,
            a=result.a,
            finestra_irrevocabilita_fino_al=result.finestra_irrevocabilita_fino_al,
            programmato=result.programmato,
            da_rinnovo=result.da_rinnovo,
            totale=result.totale,
        )
