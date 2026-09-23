"""Spec §9's own call site: the unbilled-`work_unit`-to-`InvoiceLine` assembly step.

Proves the Done-when literally: an approved, worked day becomes an ordinary
`InvoiceLine` through `InvoiceService.replace_lines`, with no schema change to
`Invoice`/`InvoiceLine`, and is never billed twice.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import (
    assemble_unbilled_work_units_into_new_invoice,
    bind_work_units,
    unbilled_work_unit_lines,
)
from pigrocrm.core.work_units.models import WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")


def _install_fiscal_and_emitter_profiles(session: Session) -> None:
    """The same minimal setup `tests/conftest.py::_invoice_service` installs --
    duplicated here rather than imported, since `conftest` is an ambiguous module
    name across this repository's three test roots (that file's own
    `extract_pdf_text` docstring explains why)."""
    from pigrocrm.core.emitter.repository import EmitterProfileRepository
    from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
    from pigrocrm.core.emitter.service import EmitterProfileService
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    if FiscalProfileRepository(session).get() is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    if EmitterProfileRepository(session).get() is None:
        EmitterProfileService(session).upsert(
            EmitterProfileUpsert(
                ragione_sociale="Studio Rossi",
                partita_iva="01234567890",
                codice_fiscale="HMCRFT00A01H501K",
                indirizzo="Via Vittorio Veneto 12",
                cap="20124",
                comune="Milano",
                provincia="MI",
                nazione="IT",
                email="mario@example.com",
            ),
            ADMIN,
        )


def _contract(db_session: Session) -> Contract:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _rate_card(db_session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valido_da": date(2026, 1, 1),
        "valido_a": None,
        "tipo": "giornaliero",
        "importo": Decimal("500.00"),
        "unita": "giorno",
    }
    payload.update(overrides)
    rate_card = RateCard(**payload)  # type: ignore[arg-type]
    db_session.add(rate_card)
    db_session.flush()
    return rate_card


def _work_unit(db_session: Session, contract: Contract, **overrides: object) -> WorkUnit:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "data": date(2026, 3, 5),
        "quantita": Decimal("1.00"),
        "descrizione": "Giornata di consulenza",
        "stato": "lavorato",
    }
    payload.update(overrides)
    work_unit = WorkUnit(**payload)  # type: ignore[arg-type]
    db_session.add(work_unit)
    db_session.flush()
    return work_unit


def _bare_invoice_line(db_session: Session, contract: Contract) -> InvoiceLine:
    """A real `invoice_lines` row, built directly rather than through
    `InvoiceService` -- enough to satisfy `work_units.invoice_line_id`'s own FK for a
    test that is not itself about invoice construction."""
    invoice = Invoice(customer_id=contract.customer_id, tipo="fattura", stato="bozza")
    db_session.add(invoice)
    db_session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        numero_linea=1,
        descrizione="Riga esistente",
        quantita=Decimal("1.000000"),
        prezzo_unitario=Decimal("1.000000"),
        prezzo_totale=Decimal("1.00"),
        aliquota_iva=Decimal("22.00"),
    )
    db_session.add(line)
    db_session.flush()
    return line


# ---- unbilled_work_unit_lines ---------------------------------------------------------


def test_two_days_in_the_same_month_on_the_same_rate_card_become_one_line(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, data=date(2026, 3, 5))
    _work_unit(db_session, contract, data=date(2026, 3, 12))

    righe, gruppi = unbilled_work_unit_lines(db_session, contract.id)

    assert len(righe) == 1
    assert righe[0].quantita == Decimal("2.00")
    assert righe[0].prezzo_unitario == Decimal("500.00")
    assert righe[0].unita_misura == "giorno"
    assert "marzo" in righe[0].descrizione.lower()
    assert len(gruppi[0]) == 2


def test_days_in_different_months_become_separate_lines(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, data=date(2026, 3, 5))
    _work_unit(db_session, contract, data=date(2026, 4, 2))

    righe, gruppi = unbilled_work_unit_lines(db_session, contract.id)

    assert len(righe) == 2
    assert sorted(r.quantita for r in righe) == [Decimal("1.00"), Decimal("1.00")]
    assert sorted(len(g) for g in gruppi) == [1, 1]


def test_days_under_different_rate_cards_in_the_same_month_stay_separate(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract, valido_da=date(2026, 1, 1), valido_a=date(2026, 3, 15))
    _rate_card(
        db_session, contract, valido_da=date(2026, 3, 16), valido_a=None, importo=Decimal("600.00")
    )
    _work_unit(db_session, contract, data=date(2026, 3, 5))
    _work_unit(db_session, contract, data=date(2026, 3, 20))

    righe, _ = unbilled_work_unit_lines(db_session, contract.id)

    assert sorted(r.prezzo_unitario for r in righe) == [Decimal("500.00"), Decimal("600.00")]


def test_a_proposed_day_is_never_gathered(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, stato="proposto")

    righe, gruppi = unbilled_work_unit_lines(db_session, contract.id)
    assert righe == []
    assert gruppi == []


def test_an_already_billed_day_is_never_gathered_twice(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    existing_line = _bare_invoice_line(db_session, contract)
    _work_unit(db_session, contract, invoice_line_id=existing_line.id)

    righe, gruppi = unbilled_work_unit_lines(db_session, contract.id)
    assert righe == []
    assert gruppi == []


def test_a_day_with_no_covering_rate_card_is_refused_not_invented(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract, valido_da=date(2026, 1, 1), valido_a=date(2026, 2, 28))
    _work_unit(db_session, contract, data=date(2026, 3, 5))  # outside the card's validity

    with pytest.raises(ValidationFailed):
        unbilled_work_unit_lines(db_session, contract.id)


def test_no_unbilled_days_returns_empty_not_an_error(db_session: Session) -> None:
    contract = _contract(db_session)
    righe, gruppi = unbilled_work_unit_lines(db_session, contract.id)
    assert righe == []
    assert gruppi == []


# ---- bind_work_units --------------------------------------------------------------


def test_bind_work_units_sets_invoice_line_id_per_group(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _work_unit(db_session, contract, data=date(2026, 3, 1))
    second = _work_unit(db_session, contract, data=date(2026, 3, 2))
    line_a = _bare_invoice_line(db_session, contract)
    line_b = _bare_invoice_line(db_session, contract)

    bind_work_units(db_session, [[first.id], [second.id]], [line_a.id, line_b.id])
    db_session.flush()

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.invoice_line_id == line_a.id
    assert second.invoice_line_id == line_b.id


# ---- assemble_unbilled_work_units_into_new_invoice, end to end ------------------------


def test_assemble_creates_an_invoice_line_through_replace_lines_and_binds_the_days(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    first = _work_unit(db_session, contract, data=date(2026, 3, 5))
    second = _work_unit(db_session, contract, data=date(2026, 3, 12))

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    assert invoice.customer_id == contract.customer_id
    assert invoice.imponibile == Decimal("1000.00")  # 2 giorni * 500.00

    lines = list(
        db_session.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
        ).scalars()
    )
    assert len(lines) == 1
    assert lines[0].prezzo_totale == Decimal("1000.00")

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.invoice_line_id == lines[0].id
    assert second.invoice_line_id == lines[0].id


def test_assemble_never_bills_the_same_day_twice(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, data=date(2026, 3, 5))

    assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)

    with pytest.raises(ValidationFailed):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)


def test_assemble_refuses_a_contract_with_nothing_to_bill(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)


def test_assemble_raises_not_found_for_an_unknown_contract(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    with pytest.raises(NotFound):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, uuid4(), ADMIN)
