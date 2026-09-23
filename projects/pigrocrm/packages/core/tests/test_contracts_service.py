from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from orologio import OGGI_IN_ITALIA, congela
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.schemas import ContractCreate, ContractListQuery, RateCardCreate
from pigrocrm.core.contracts.service import ContractService, RateCardService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice

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


# ---- concentration_cap (REB-352 §1.5) ----------------------------------------------


def _invoice(
    db_session: Session, customer_id: UUID, *, imponibile: str, data_emissione: date
) -> None:
    row = Invoice(
        customer_id=customer_id,
        tipo="fattura",
        stato="emessa",
        stato_pagamento="da_incassare",
        imponibile=Decimal(imponibile),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal(imponibile),
        data_emissione=data_emissione,
        tipo_documento="TD01",
        divisa="EUR",
        custom_fields={},
    )
    db_session.add(row)
    db_session.flush()


def test_concentration_cap_computes_the_clients_share_of_the_anniversary_year(
    db_session: Session,
) -> None:
    """The window anchors to the contract's own `inizio` (10 March), not the calendar
    year: an invoice dated before that anniversary is out of the window even though
    it falls inside the same `data_emissione.year`."""
    customer = _customer(db_session)
    other = Customer(ragione_sociale="Altro Cliente")
    db_session.add(other)
    db_session.flush()
    contract = ContractService(db_session).create(
        _create_payload(customer.id, inizio=date(2026, 3, 10)), ADMIN
    )

    _invoice(db_session, customer.id, imponibile="300.00", data_emissione=date(2026, 4, 1))
    _invoice(db_session, other.id, imponibile="700.00", data_emissione=date(2026, 4, 1))
    # Before the anniversary: excluded from the window even though it is the same year.
    _invoice(db_session, customer.id, imponibile="9000.00", data_emissione=date(2026, 2, 1))

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert cap.contract_id == contract.id
    assert cap.customer_id == customer.id
    assert (cap.periodo_da, cap.periodo_a) == (date(2026, 3, 10), date(2027, 3, 9))
    assert cap.ricavi_cliente == Decimal("300.00")
    assert cap.ricavi_totali == Decimal("1000.00")
    assert cap.quota == pytest.approx(0.3)
    assert cap.soglia is None
    assert cap.superata is None


def test_concentration_cap_is_zero_when_nothing_has_been_invoiced_in_the_window(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 6, 1))

    assert cap.ricavi_cliente == Decimal("0.00")
    assert cap.ricavi_totali == Decimal("0.00")
    assert cap.quota == 0.0


def test_concentration_cap_reports_whether_a_threshold_is_exceeded(db_session: Session) -> None:
    customer = _customer(db_session)
    other = Customer(ragione_sociale="Altro Cliente")
    db_session.add(other)
    db_session.flush()
    contract = ContractService(db_session).create(_create_payload(customer.id), ADMIN)
    _invoice(db_session, customer.id, imponibile="300.00", data_emissione=date(2026, 4, 1))
    _invoice(db_session, other.id, imponibile="700.00", data_emissione=date(2026, 4, 1))

    below = ContractService(db_session).concentration_cap(
        contract.id, ADMIN, as_of=date(2026, 6, 1), soglia=0.5
    )
    above = ContractService(db_session).concentration_cap(
        contract.id, ADMIN, as_of=date(2026, 6, 1), soglia=0.2
    )

    assert below.soglia == 0.5 and below.superata is False
    assert above.soglia == 0.2 and above.superata is True


def test_concentration_cap_defaults_as_of_to_today(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = _customer(db_session)
    contract = ContractService(db_session).create(
        _create_payload(customer.id, inizio=date(2025, 6, 1)), ADMIN
    )

    cap = ContractService(db_session).concentration_cap(contract.id, ADMIN)

    assert cap.periodo_da == date(2025, 6, 1)
    assert OGGI_IN_ITALIA.year == 2026  # sanity: the frozen "today" is inside this window


def test_concentration_cap_rejects_an_as_of_before_the_contracts_own_start(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    contract = ContractService(db_session).create(
        _create_payload(customer.id, inizio=date(2026, 3, 10)), ADMIN
    )

    with pytest.raises(ValidationFailed) as excinfo:
        ContractService(db_session).concentration_cap(contract.id, ADMIN, as_of=date(2026, 1, 1))
    assert excinfo.value.details["field"] == "as_of"


def test_concentration_cap_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ContractService(db_session).concentration_cap(uuid4(), ADMIN, as_of=date(2026, 1, 1))
