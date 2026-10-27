from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contracts import projection
from pigrocrm.core.contracts.dates import anniversary_year_bounds
from pigrocrm.core.contracts.models import ClauseNote, Contract, RateCard, RenewalAssumption
from pigrocrm.core.contracts.renewal import RenewalRefused, carried_cards, renewed_term
from pigrocrm.core.contracts.repository import (
    ContractRepository,
    RateCardRepository,
    RenewalAssumptionRepository,
)
from pigrocrm.core.contracts.schemas import (
    CONTRACT_SORTS,
    ClauseNoteCreate,
    ClauseNoteRead,
    ClauseNoteUpdate,
    ConcentrationFigures,
    ContractCapSimulation,
    ContractConcentrationCap,
    ContractCreate,
    ContractDetail,
    ContractListQuery,
    ContractPage,
    ContractProjectionQuery,
    ContractProjectionRead,
    ContractRead,
    ContractRenew,
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
from pigrocrm.core.money import round_money
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`), matching
# CustomerService.ENTITY/DealService.ENTITY exactly -- see those classes for why a
# plain `str` would fail mypy strict at `specs_for`'s own call site.
ENTITY: EntityType = "contract"


# The columns a renewal does not copy from the contract it renews: the row's own identity
# and bookkeeping, the term (which the renewal decides), the state (the successor is
# `attivo`, whatever the contract it renews was) and the link back.
_NOT_RENEWED = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "inizio",
        "fine",
        "stato",
        "contratto_precedente_id",
    }
)


def _share(tetto: Decimal) -> float:
    """A cap stored as a percentage (`50.00`) as the share in [0, 1] every `quota` is."""
    return float(tetto / 100)


def _quota(ricavi_cliente: Decimal, ricavi_totali: Decimal) -> float:
    """The customer's share of the total, `0.0` when there is nothing to scale against:
    the same reading every `quota` on this schema carries."""
    return float(ricavi_cliente / ricavi_totali) if ricavi_totali > 0 else 0.0


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

    def detail(self, contract_id: UUID, actor: Actor) -> ContractDetail:
        """`get`, with what hangs off the one contract: its clause notes, oldest first, and
        the contract that renewed it. What the contract page opens and `get_contract`
        answers, so an agent reads the person's decisions about the clauses with the
        terms they are about."""
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        notes = self.session.scalars(
            select(ClauseNote)
            .where(ClauseNote.contract_id == contract_id)
            .order_by(ClauseNote.created_at, ClauseNote.id)
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

    def renew(self, contract_id: UUID, data: ContractRenew, actor: Actor) -> ContractRead:
        """REB-763: the contract that renews `contract_id`. The successor carries the
        contract's terms as they stand (every column but the ones named in
        `_NOT_RENEWED`, so a column added later travels by default instead of silently
        resetting on every renewal) on the renewed term (`renewal.renewed_term`), is
        `attivo`, and is linked back through `contratto_precedente_id`. The rate cards
        that reach the new term go across with the validity they have on it
        (`renewal.carried_cards`); the contract's own cards are not touched, so the history
        of what was charged stays on the contract it was charged under.

        Not carried, because each is a fact about the term that is ending: the renewal
        assumption (a belief about revenue beyond *that* term), the expenses and the days,
        and the clause notes, which read the words of a signed document that the renewed
        term may have replaced. The contract it renews keeps its `stato`: nothing here
        decides that a term is over.

        A contract renews once (the unique key on the link is the database's own word, the
        check below the sentence the person reads) and only when it has an end to renew
        from.
        """
        actor.require_write("renew_contract")
        contract = self.repo.get_locked(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        existing = self.session.scalar(
            select(Contract.id).where(Contract.contratto_precedente_id == contract_id)
        )
        if existing is not None:
            raise Conflict(
                ENTITY,
                "il contratto è già stato rinnovato",
                contract_id=str(contract_id),
                successor_id=str(existing),
            )
        try:
            inizio, fine = renewed_term(
                inizio=contract.inizio,
                fine=contract.fine,
                nuovo_inizio=data.inizio,
                nuova_fine=data.fine,
                durata=None if data.durata is None else (data.durata.conteggio, data.durata.unita),
            )
        except RenewalRefused as refused:
            raise ValidationFailed(
                ENTITY, refused.field, str(refused), expected=refused.expected
            ) from refused
        # The azienda of the customer as it is now, as `create` takes it, and never a
        # deactivated one: a customer moved since the contract began renews on the azienda
        # it has moved to, and an archived customer has nothing to renew for.
        customer = self.customers.get(contract.customer_id)
        if customer is None:
            raise NotFound("customer", contract.customer_id)
        azienda_id = LegalEntityService(self.session).inherited(customer.azienda_id, ENTITY)

        carried = {
            column.key: deepcopy(getattr(contract, column.key))
            for column in sa_inspect(Contract).column_attrs
            if column.key not in _NOT_RENEWED
        }
        if data.titolo is not None:
            carried["titolo"] = data.titolo
        carried["azienda_id"] = azienda_id
        successor = Contract(
            **carried,
            inizio=inizio,
            fine=fine,
            stato="attivo",
            contratto_precedente_id=contract.id,
        )
        try:
            self.session.add(successor)
            self.session.flush()
        except IntegrityError as exc:
            # Two renewals racing: the lock above makes the second read the first's row, so
            # this is the unique key as a last resort.
            self.session.rollback()
            raise Conflict(
                ENTITY, "il contratto è già stato rinnovato", contract_id=str(contract_id)
            ) from exc
        for card, valido_da, valido_a in carried_cards(
            RateCardRepository(self.session).list_for_contract(contract.id), inizio
        ):
            self.session.add(
                RateCard(
                    contract_id=successor.id,
                    valido_da=valido_da,
                    valido_a=valido_a,
                    tipo=card.tipo,
                    importo=card.importo,
                    unita=card.unita,
                    frazioni_ammesse=list(card.frazioni_ammesse),
                    ore_minime=card.ore_minime,
                    periodo_erogazione=card.periodo_erogazione,
                )
            )
        self.session.flush()
        self.activities.record(
            ENTITY,
            successor.id,
            "created",
            actor,
            {"titolo": successor.titolo, "rinnova": str(contract.id)},
        )
        self.activities.record(
            ENTITY,
            contract.id,
            "renewed",
            actor,
            {
                "successore": str(successor.id),
                "inizio": inizio.isoformat(),
                "fine": fine.isoformat(),
            },
        )
        self.session.commit()
        return ContractRead.model_validate(successor)

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
        exposes -- no `require_write`. `soglia` is a caller-supplied share in [0, 1]
        and is never stored; without one the share is compared with the contract's own
        cap when it has one (REB-761), and `superata` stays `None` when it has neither.
        """
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        periodo_da, periodo_a = self._anniversary_window(contract, as_of)
        soglia_da_contratto = soglia is None and contract.tetto_quota_cliente is not None
        if soglia is None and contract.tetto_quota_cliente is not None:
            soglia = _share(contract.tetto_quota_cliente)
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
            quota = _quota(ricavi_cliente, ricavi_totali)
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
            soglia_da_contratto=soglia_da_contratto,
            valute=valute,
        )

    def simulate_cap(
        self, contract_id: UUID, aggiunta: Decimal, data_prevista: date | None, actor: Actor
    ) -> ContractCapSimulation:
        """REB-761: the contract's cap against its customer's share after a synthetic
        addition, the «ci sta?» simulator's own reading (`AnalyticsService.simulate_ceiling`
        calls this when it is given a contract). The addition is revenue of this customer
        on the anniversary year containing `data_prevista` (today when not given), so it
        raises the customer's figure and the total together. Nothing is written.

        A contract with no cap has nothing to be checked against, and says so naming the
        field instead of answering that everything fits.
        """
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        if contract.tetto_quota_cliente is None:
            raise ValidationFailed(
                ENTITY,
                "tetto_quota_cliente",
                "il contratto non ha un tetto sulla quota del cliente",
                expected="un contratto con un tetto, da impostare sul contratto",
            )
        periodo_da, periodo_a = self._anniversary_window(contract, data_prevista, "data_prevista")
        revenue = self.analytics.revenue_for_customer_in_window(
            contract.customer_id, periodo_da, periodo_a, azienda_id=contract.azienda_id
        )
        cliente, totale = next(
            ((c, t) for divisa, c, t in revenue if divisa == contract.divisa),
            (Decimal("0.00"), Decimal("0.00")),
        )
        tetto = _share(contract.tetto_quota_cliente)
        quota_simulata = _quota(cliente + aggiunta, totale + aggiunta)
        return ContractCapSimulation(
            contract_id=contract.id,
            titolo=contract.titolo,
            customer_id=contract.customer_id,
            divisa=contract.divisa,
            periodo_da=periodo_da,
            periodo_a=periodo_a,
            tetto=tetto,
            ricavi_cliente_attuali=cliente,
            ricavi_totali_attuali=totale,
            quota_attuale=_quota(cliente, totale),
            ricavi_cliente_simulati=round_money(cliente + aggiunta),
            ricavi_totali_simulati=round_money(totale + aggiunta),
            quota_simulata=quota_simulata,
            rientra=quota_simulata < tetto,
        )

    @staticmethod
    def _anniversary_window(
        contract: Contract, as_of: date | None, field: str = "as_of"
    ) -> tuple[date, date]:
        """The anniversary year of `contract` that contains `as_of` (today when not given):
        the window a cap resets on, never the calendar year."""
        reference = as_of if as_of is not None else today_local()
        if reference < contract.inizio:
            raise ValidationFailed(
                ENTITY,
                field,
                "precede l'inizio del contratto",
                expected=f">= {contract.inizio.isoformat()}",
            )
        return anniversary_year_bounds(contract.inizio, reference)

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


class ClauseNoteService:
    """The readings of a contract's clauses (REB-762): written, corrected and listed, never
    deleted (see `ClauseNote`). Each write is on the contract's timeline, with the clause it
    is about and not the words, which can run to pages."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.contracts = ContractRepository(session)
        self.activities = ActivityService(session)

    def _contract(self, contract_id: UUID) -> Contract:
        contract = self.contracts.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        return contract

    def create(self, contract_id: UUID, data: ClauseNoteCreate, actor: Actor) -> ClauseNoteRead:
        actor.require_write("create_clause_note")
        self._contract(contract_id)
        note = ClauseNote(**data.model_dump(), contract_id=contract_id)
        self.session.add(note)
        self.session.flush()
        self.activities.record(
            ENTITY,
            contract_id,
            "clause_note_added",
            actor,
            {"riferimento_clausola": note.riferimento_clausola},
        )
        self.session.commit()
        return ClauseNoteRead.model_validate(note)

    def update(
        self, contract_id: UUID, note_id: UUID, data: ClauseNoteUpdate, actor: Actor
    ) -> ClauseNoteRead:
        actor.require_write("update_clause_note")
        self._contract(contract_id)
        note = self.session.get(ClauseNote, note_id)
        if note is None or note.contract_id != contract_id:
            raise NotFound("clause_note", note_id)
        changes = supplied_changes(data)
        reject_cleared_columns("clause_note", ClauseNote, changes)
        if not changes:
            return ClauseNoteRead.model_validate(note)
        for key, value in changes.items():
            setattr(note, key, value)
        self.session.flush()
        self.activities.record(
            ENTITY,
            contract_id,
            "clause_note_updated",
            actor,
            {"riferimento_clausola": note.riferimento_clausola, "changed": sorted(changes)},
        )
        self.session.commit()
        return ClauseNoteRead.model_validate(note)

    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[ClauseNoteRead]:
        self._contract(contract_id)
        notes = self.session.scalars(
            select(ClauseNote)
            .where(ClauseNote.contract_id == contract_id)
            .order_by(ClauseNote.created_at, ClauseNote.id)
        )
        return [ClauseNoteRead.model_validate(note) for note in notes]


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
