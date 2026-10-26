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
from pigrocrm.core.errors import AgentForbidden, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceAnnul, InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import (
    assemble_unbilled_work_units_into_new_invoice,
    billable_days,
    bind_work_units,
    unbilled_work_unit_lines,
)
from pigrocrm.core.work_units.models import WorkUnit, WorkUnitTransition

ADMIN = Actor(id=None, type="system", role="admin")


def _install_fiscal_and_emitter_profiles(session: Session) -> None:
    """The same minimal setup `tests/conftest.py::_invoice_service` installs --
    duplicated here rather than imported, since `conftest` is an ambiguous module
    name across this repository's three test roots (that file's own
    `extract_pdf_text` docstring explains why)."""
    from pigrocrm.core.emitter.repository import LegalEntityRepository
    from pigrocrm.core.emitter.schemas import LegalEntityUpsert
    from pigrocrm.core.emitter.service import LegalEntityService
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    # The azienda first, always (REB-615: a bare default is already seeded and this
    # fills in its identity), then its fiscal profile when it has none.
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
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
    azienda = LegalEntityRepository(session).default()
    assert azienda is not None
    if FiscalProfileRepository(session).get(azienda.id) is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)


def _contract(db_session: Session) -> Contract:
    # Enough identity to appear on an issued document: some tests below issue the invoice.
    customer = Customer(
        ragione_sociale=f"ACME {uuid4()}",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
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

    bind_work_units(db_session, [[first.id], [second.id]], [line_a.id, line_b.id], ADMIN)
    db_session.flush()

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.invoice_line_id == line_a.id
    assert second.invoice_line_id == line_b.id
    # The day reads «fatturato», not just linked: a day's page offers its moves from the
    # state, and the one that keeps a billed day from being revoked is this.
    assert first.stato == second.stato == "fatturato"


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
    assert first.stato == second.stato == "fatturato"


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


def test_assemble_takes_only_the_days_of_the_period_asked_for(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    october = _work_unit(db_session, contract, data=date(2026, 10, 7))
    november = _work_unit(db_session, contract, data=date(2026, 11, 4))

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN, date(2026, 10, 1), date(2026, 10, 31)
    )

    assert invoice.imponibile == Decimal("500.00")
    db_session.refresh(october)
    db_session.refresh(november)
    assert (october.stato, november.stato) == ("fatturato", "lavorato")
    assert november.invoice_line_id is None


def test_an_agent_never_bills_a_contracts_days(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    agent = Actor(id=None, type="mcp", role="admin")

    with pytest.raises(AgentForbidden):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, agent)

    db_session.refresh(day)
    assert (day.stato, day.invoice_line_id) == ("lavorato", None)


# ---- billable_days: what the page shows before anybody presses the button ---------------


def test_the_preview_is_the_invoice_the_button_would_make(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, data=date(2026, 10, 7))
    _work_unit(db_session, contract, data=date(2026, 10, 8), quantita=Decimal("0.50"))
    _work_unit(db_session, contract, data=date(2026, 11, 3))
    _work_unit(db_session, contract, data=date(2026, 11, 4), stato="proposto")

    preview = billable_days(db_session, contract.id)

    assert preview.fatturabile is True
    assert preview.mesi == ["2026-10", "2026-11"]
    assert [(r.mese, r.giornate, r.quantita, r.importo) for r in preview.righe] == [
        ("2026-10", 2, Decimal("1.50"), Decimal("750.00")),
        ("2026-11", 1, Decimal("1.00"), Decimal("500.00")),
    ]
    assert (preview.giornate, preview.quantita, preview.imponibile) == (
        3,
        Decimal("2.50"),
        Decimal("1250.00"),
    )

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    assert invoice.imponibile == preview.imponibile
    # Billed: nothing left to show.
    assert billable_days(db_session, contract.id).righe == []


def test_the_preview_names_the_days_no_rate_card_covers_instead_of_refusing(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract, valido_da=date(2026, 1, 1), valido_a=date(2026, 2, 28))
    _work_unit(db_session, contract, data=date(2026, 2, 3))
    _work_unit(db_session, contract, data=date(2026, 3, 5))

    preview = billable_days(db_session, contract.id)

    assert preview.fatturabile is False
    assert preview.senza_tariffa == [date(2026, 3, 5)]
    assert [r.mese for r in preview.righe] == ["2026-02"]


def test_the_preview_counts_the_days_worked_without_the_approval_the_contract_requires(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    contract.requires_prior_approval = True
    db_session.flush()
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, data=date(2026, 3, 5))  # no approval: redirected

    preview = billable_days(db_session, contract.id)

    assert preview.senza_approvazione == 1
    assert preview.righe == []
    assert preview.fatturabile is False


def test_the_preview_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        billable_days(db_session, uuid4())


# ---- the days come back when the invoice they were on goes away -------------------------


def _states_of(db_session: Session, work_unit: WorkUnit) -> list[str]:
    return list(
        db_session.scalars(
            select(WorkUnitTransition.stato_nuovo)
            .where(WorkUnitTransition.work_unit_id == work_unit.id)
            .order_by(WorkUnitTransition.seq)
        )
    )


def test_deleting_the_draft_gives_its_days_back(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    InvoiceService(db_session, local_storage).soft_delete(invoice.id, ADMIN)

    db_session.refresh(day)
    assert (day.stato, day.invoice_line_id) == ("lavorato", None)
    # The history says why it came back: the dispute in the middle is the graph's only way
    # home, not something anybody raised.
    assert _states_of(db_session, day) == ["lavorato", "fatturato", "contestato", "lavorato"]
    assert billable_days(db_session, contract.id).giornate == 1


def test_annulling_the_invoice_gives_its_days_back(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service = InvoiceService(db_session, local_storage)
    service.issue(invoice.id, InvoiceIssue(), ADMIN)
    db_session.refresh(day)
    assert day.stato == "fatturato"

    service.annul(invoice.id, InvoiceAnnul(motivo="importo errato"), ADMIN)

    db_session.refresh(day)
    assert (day.stato, day.invoice_line_id) == ("lavorato", None)


def test_a_paid_day_stays_where_it_is_when_its_invoice_is_annulled(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service = InvoiceService(db_session, local_storage)
    service.issue(invoice.id, InvoiceIssue(), ADMIN)
    db_session.refresh(day)
    day.stato = "pagato"
    db_session.flush()

    service.annul(invoice.id, InvoiceAnnul(motivo="importo errato"), ADMIN)

    db_session.refresh(day)
    assert day.stato == "pagato"
    assert day.invoice_line_id is not None
