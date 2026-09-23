from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.schemas import ContractCreate, ContractListQuery, RateCardCreate
from pigrocrm.core.contracts.service import ContractService, RateCardService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed

ADMIN = Actor(id=None, type="system", role="admin")


def _customer(db_session: Session) -> Customer:
    customer = Customer(ragione_sociale="ACME S.r.l.")
    db_session.add(customer)
    db_session.flush()
    return customer


def _create_payload(customer_id: object, **overrides: object) -> ContractCreate:
    payload: dict[str, object] = {
        "customer_id": customer_id,
        "titolo": "Consulenza CTO",
        "inizio": date(2026, 1, 1),
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"tipo": "non_rimborsabile"},
    }
    payload.update(overrides)
    return ContractCreate(**payload)  # type: ignore[arg-type]


# ---- Done-when: created, read and listed through the API's own service layer ------


def test_a_contract_can_be_created_read_and_listed(db_session: Session) -> None:
    customer = _customer(db_session)
    service = ContractService(db_session)

    created = service.create(_create_payload(customer.id), ADMIN)
    assert created.stato == "bozza"

    read = service.get(created.id, ADMIN)
    assert read.id == created.id
    assert read.titolo == "Consulenza CTO"

    page = service.list(ContractListQuery(customer_id=customer.id), ADMIN)
    assert [c.id for c in page.items] == [created.id]


def test_create_rejects_an_unknown_customer(db_session: Session) -> None:
    service = ContractService(db_session)
    with pytest.raises(NotFound):
        service.create(_create_payload(uuid4()), ADMIN)


def test_create_rejects_half_set_payment_terms_with_a_clean_validation_error(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    service = ContractService(db_session)
    payload = _create_payload(customer.id).model_copy(update={"giorni_pagamento": 30})
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(payload, ADMIN)
    assert excinfo.value.details["field"] == "giorni_pagamento"


def test_create_rejects_a_missing_renewal_notice_with_a_clean_validation_error(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    service = ContractService(db_session)
    payload = _create_payload(customer.id).model_copy(update={"tipo_rinnovo": "esplicito"})
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(payload, ADMIN)
    assert excinfo.value.details["field"] == "preavviso_rinnovo_giorni"


def test_get_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    service = ContractService(db_session)
    with pytest.raises(NotFound):
        service.get(uuid4(), ADMIN)


def test_contract_create_rejects_fine_before_inizio() -> None:
    """An inverted validity period is never even constructed -- Greptile flagged
    this as unenforced; ck_contracts_fine_ordered is the database's own backstop,
    but the schema is what gives a clean 422 instead of a raw IntegrityError."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="fine"):
        _create_payload(uuid4(), fine=date(2025, 1, 1))


def test_rate_card_create_rejects_valido_a_before_valido_da() -> None:
    """Greptile: the service's own `except IntegrityError` cannot distinguish an
    inverted range from a genuine overlap, so this must never reach the database
    at all -- caught here, at construction, not translated into a Conflict."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="valido_a"):
        _rate_card_payload(valido_da=date(2026, 6, 1), valido_a=date(2026, 1, 1))


# ---- rate cards, through the service layer -----------------------------------------


def _rate_card_payload(**overrides: object) -> RateCardCreate:
    payload: dict[str, object] = {
        "valido_da": date(2026, 1, 1),
        "tipo": "ricorrente_fisso",
        "importo": Decimal("1000.00"),
        "unita": "mese",
    }
    payload.update(overrides)
    return RateCardCreate(**payload)  # type: ignore[arg-type]


def test_a_rate_card_can_be_created_and_listed_for_its_contract(db_session: Session) -> None:
    customer = _customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    created = rate_cards.create(contract.id, _rate_card_payload(), ADMIN)
    assert created.contract_id == contract.id

    listed = rate_cards.list_for_contract(contract.id, ADMIN)
    assert [c.id for c in listed] == [created.id]


def test_rate_card_create_rejects_an_unknown_contract(db_session: Session) -> None:
    rate_cards = RateCardService(db_session)
    with pytest.raises(NotFound):
        rate_cards.create(uuid4(), _rate_card_payload(), ADMIN)


def test_rate_card_create_turns_an_overlap_into_a_clean_conflict_not_a_raw_db_error(
    db_session: Session,
) -> None:
    """Done-when: "a second rate card overlapping an existing one on the same
    contract is refused by the database, not by a service-level check" -- the
    service still owes the caller a clean domain error, not a raw IntegrityError."""
    customer = _customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    rate_cards.create(
        contract.id,
        _rate_card_payload(valido_da=date(2026, 1, 1), valido_a=date(2026, 6, 30)),
        ADMIN,
    )
    with pytest.raises(Conflict):
        rate_cards.create(
            contract.id,
            _rate_card_payload(valido_da=date(2026, 6, 1), valido_a=date(2026, 12, 31)),
            ADMIN,
        )

    # The session must still be usable after the rollback -- the same guarantee
    # EmitterProfileService.upsert/FiscalProfileService.upsert already give their
    # own callers -- and only the first, non-overlapping card actually exists.
    surviving = rate_cards.list_for_contract(contract.id, ADMIN)
    assert [c.valido_a for c in surviving] == [date(2026, 6, 30)]


def test_rate_card_create_rejects_ore_minime_outside_orario_with_a_clean_validation_error(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    contracts = ContractService(db_session)
    rate_cards = RateCardService(db_session)
    contract = contracts.create(_create_payload(customer.id), ADMIN)

    with pytest.raises(ValidationFailed) as excinfo:
        rate_cards.create(
            contract.id,
            _rate_card_payload(tipo="giornaliero", ore_minime=Decimal("4.00")),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "ore_minime"


def test_rate_card_list_for_an_unknown_contract_is_not_found(db_session: Session) -> None:
    rate_cards = RateCardService(db_session)
    with pytest.raises(NotFound):
        rate_cards.list_for_contract(uuid4(), ADMIN)
