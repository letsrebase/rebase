from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.repository import ContractRepository, RateCardRepository
from pigrocrm.core.contracts.schemas import (
    CONTRACT_SORTS,
    ContractCreate,
    ContractListQuery,
    ContractPage,
    ContractRead,
    RateCardCreate,
    RateCardRead,
)
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.db import encode_cursor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields

# Typed as the fields module's own EntityType (not a bare `str`), matching
# CustomerService.ENTITY/DealService.ENTITY exactly -- see those classes for why a
# plain `str` would fail mypy strict at `specs_for`'s own call site.
ENTITY: EntityType = "contract"


def _check_payment_terms_together(payload: dict[str, Any]) -> None:
    """Mirrors `ck_contracts_payment_terms_together`: a contract states both of its
    own payment-term facts or neither, never one paired with a term it never set."""
    if (payload.get("giorni_pagamento") is None) != (payload.get("pagamento_fine_mese") is None):
        raise ValidationFailed(
            ENTITY,
            "giorni_pagamento",
            "giorni_pagamento e pagamento_fine_mese vanno impostati insieme, o nessuno dei due",
            expected="entrambi valorizzati oppure entrambi assenti",
        )


def _check_renewal_notice(payload: dict[str, Any]) -> None:
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

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        """Used by `create` only: `values` is the *complete* desired set of custom
        fields for a brand-new row, so it is validated against every active
        definition. Mirrors CustomerService._validated_custom/DealService.
        _validated_custom exactly."""
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def create(self, data: ContractCreate, actor: Actor) -> ContractRead:
        actor.require_write("create_contract")
        payload = data.model_dump()
        _check_payment_terms_together(payload)
        _check_renewal_notice(payload)

        if self.customers.get(payload["customer_id"]) is None:
            raise NotFound("customer", payload["customer_id"])
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})

        contract = self.repo.add(Contract(**payload))
        self.activities.record(
            ENTITY, contract.id, "created", actor, {"titolo": contract.titolo}
        )
        self.session.commit()
        return ContractRead.model_validate(contract)

    def get(self, contract_id: UUID, actor: Actor) -> ContractRead:
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        return ContractRead.model_validate(contract)

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


def _check_rate_card_shape(payload: dict[str, Any]) -> None:
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

    def create(self, contract_id: UUID, data: RateCardCreate, actor: Actor) -> RateCardRead:
        actor.require_write("create_rate_card")
        contract = self.contracts.get(contract_id)
        if contract is None:
            raise NotFound("contract", contract_id)

        payload = data.model_dump()
        _check_rate_card_shape(payload)
        payload["contract_id"] = contract_id

        rate_card = RateCard(**payload)
        self.session.add(rate_card)
        try:
            self.session.flush()
        except IntegrityError as exc:
            # ck_rate_cards_no_overlap is the real authority here (Done-when: "refused
            # by the database, not by a service-level check") -- this only turns the
            # raw exclusion violation into this project's own clean error, the same
            # way EmitterProfileService.upsert/FiscalProfileService.upsert already do
            # for their own singleton race.
            self.session.rollback()
            raise Conflict(
                "rate_card",
                "il periodo di validità si sovrappone a un'altra scheda tariffaria di questo contratto",
                contract_id=str(contract_id),
            ) from exc

        self.activities.record(
            "contract",
            contract_id,
            "rate_card_added",
            actor,
            {"tipo": rate_card.tipo, "valido_da": str(rate_card.valido_da)},
        )
        self.session.commit()
        return RateCardRead.model_validate(rate_card)

    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[RateCardRead]:
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)
        return [
            RateCardRead.model_validate(card)
            for card in self.repo.list_for_contract(contract_id)
        ]
