"""AnalyticsService.days_analysis -- REB-756.

The days of a period by month and state, the realised day rate against the rate card, and
the trend of days worked without approval. What these hold the code to: a state stacks in
its own month, a day that never happened is not charted, the rate reads an invoice line
once however many days it bills, a day no card covers is counted apart instead of priced at
zero, and the slip into «senza approvazione» is dated by the transition, not by the day.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from conftest import _invoice_service
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceIssue
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import assemble_unbilled_work_units_into_new_invoice
from pigrocrm.core.work_units.models import WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")
READER = Actor(id=None, type="user", role="readonly")
ANNO = 2026


def _contract(
    session: Session, name: str = "Acme S.r.l.", *, requires_prior_approval: bool = False
) -> Contract:
    customer = Customer(
        ragione_sociale=f"{name} {uuid4().hex[:6]}",
        partita_iva="01234567890",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza",
        inizio=date(2025, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
        requires_prior_approval=requires_prior_approval,
    )
    session.add(contract)
    session.flush()
    return contract


def _card(
    session: Session, contract: Contract, importo: str = "500.00", **extra: object
) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valido_da": date(2025, 1, 1),
        "valido_a": None,
        "tipo": "giornaliero",
        "importo": Decimal(importo),
        "unita": "giorno",
    }
    payload.update(extra)
    card = RateCard(**payload)  # type: ignore[arg-type]
    session.add(card)
    session.flush()
    return card


def _day(
    session: Session, contract: Contract, giorno: date, stato: str = "lavorato", q: str = "1.00"
) -> WorkUnit:
    work_unit = WorkUnit(
        contract_id=contract.id,
        data=giorno,
        quantita=Decimal(q),
        descrizione="Giornata di consulenza",
        stato=stato,
    )
    session.add(work_unit)
    session.flush()
    session.refresh(work_unit)
    return work_unit


def _stack(month, stato: str) -> Decimal:
    return next(s.giorni for s in month.per_stato if s.stato == stato)


def test_a_state_stacks_in_its_own_month_and_a_day_that_never_happened_is_left_out(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _day(db_session, contract, date(ANNO, 1, 5), "lavorato")
    _day(db_session, contract, date(ANNO, 1, 6), "lavorato", "0.50")
    _day(db_session, contract, date(ANNO, 1, 7), "proposto")
    _day(db_session, contract, date(ANNO, 2, 3), "proposto")
    revoked = _day(db_session, contract, date(ANNO, 2, 4), "proposto")
    revoked.stato = "revocato"
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 1, 1), date(ANNO, 2, 28)
    )

    january, february = analysis.mesi
    assert (january.mese, february.mese) == ("2026-01", "2026-02")
    assert _stack(january, "lavorato") == Decimal("1.50")
    assert _stack(january, "proposto") == Decimal("1.00")
    assert january.totale == Decimal("2.50")
    assert _stack(february, "proposto") == Decimal("1.00")
    assert february.totale == Decimal("1.00")
    assert analysis.giorni_nel_periodo == Decimal("3.50")
    # Only the worked day counts as recorded: a proposal is not work done.
    assert january.registrati == Decimal("1.50")
    assert february.registrati == Decimal("0.00")
    # The tallest month is the full bar.
    assert january.quota_totale == 1.0
    assert february.quota_totale == pytest.approx(0.4)


def test_a_month_is_averaged_against_the_three_before_it_even_when_they_precede_the_window(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    for month, days in ((1, 3), (2, 3), (3, 6)):
        for day in range(1, days + 1):
            _day(db_session, contract, date(ANNO, month, day))
    for day in range(1, 5):
        _day(db_session, contract, date(ANNO, 4, day))

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 4, 1), date(ANNO, 4, 30)
    )

    (april,) = analysis.mesi
    assert april.registrati == Decimal("4.00")
    assert april.media_precedente == Decimal("4.00")  # (3 + 3 + 6) / 3
    assert april.completo is True


def test_the_current_month_is_not_complete_and_is_not_the_last_complete_month(
    db_session: Session,
) -> None:
    today = today_local()
    analysis = AnalyticsService(db_session).days_analysis(READER)

    (month,) = analysis.mesi
    assert month.mese == today.strftime("%Y-%m")
    assert month.completo is False
    assert analysis.ultimo_mese_completo is None


def test_the_realised_rate_reads_a_line_once_and_sets_it_against_the_card(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    card = _card(db_session, contract, "500.00")
    _day(db_session, contract, date(ANNO, 3, 5))
    _day(db_session, contract, date(ANNO, 3, 12))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(data_emissione=date(ANNO, 3, 31)), ADMIN)
    # The card was revised upwards after the invoice went out: the nominal figure follows
    # the card, the realised one is what was billed.
    card.importo = Decimal("600.00")
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 3, 1), date(ANNO, 3, 31)
    )

    (by_contract,) = analysis.per_contratto
    (by_customer,) = analysis.per_cliente
    for rate in (by_contract, by_customer):
        assert rate.giorni_fatturati == Decimal("2.00")
        # One line of 1000.00 bills both days: read once, not twice.
        assert rate.importo_fatturato == Decimal("1000.00")
        assert rate.tariffa_reale == Decimal("500.00")
        assert rate.tariffa_nominale == Decimal("600.00")
        assert rate.giorni_senza_listino == Decimal("0.00")
    assert analysis.giorni_fatturati == Decimal("2.00")
    assert analysis.tariffa_reale == Decimal("500.00")


def test_a_period_that_cuts_a_line_takes_only_its_days_share_of_it(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    _card(db_session, contract, "500.00")
    _day(db_session, contract, date(ANNO, 3, 5))
    _day(db_session, contract, date(ANNO, 3, 12))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(data_emissione=date(ANNO, 3, 31)), ADMIN)

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 3, 1), date(ANNO, 3, 10)
    )

    (rate,) = analysis.per_contratto
    # One of the line's two days is inside the period: half of the 1000.00 line, not all of it.
    assert rate.giorni_fatturati == Decimal("1.00")
    assert rate.importo_fatturato == Decimal("500.00")
    assert rate.tariffa_reale == Decimal("500.00")


def test_a_day_written_off_as_unbillable_still_counts_as_recorded(db_session: Session) -> None:
    contract = _contract(db_session)
    written_off = _day(db_session, contract, date(ANNO, 3, 5))
    written_off.stato = "non_fatturabile"
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 3, 1), date(ANNO, 3, 31)
    )

    (march,) = analysis.mesi
    assert _stack(march, "non_fatturabile") == Decimal("1.00")
    assert march.registrati == Decimal("1.00")


def test_another_aziendas_filter_leaves_the_days_and_the_rate_out(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    _card(db_session, contract)
    _day(db_session, contract, date(ANNO, 3, 5))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(data_emissione=date(ANNO, 3, 31)), ADMIN)

    elsewhere = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 3, 1), date(ANNO, 3, 31), uuid4()
    )
    here = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 3, 1), date(ANNO, 3, 31), contract.azienda_id
    )

    assert elsewhere.giorni_nel_periodo == Decimal("0.00")
    assert elsewhere.per_contratto == []
    assert here.giorni_nel_periodo == Decimal("1.00")
    assert len(here.per_contratto) == 1


def test_a_billed_day_no_card_covers_is_counted_apart_and_never_priced_at_zero(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    card = _card(db_session, contract, "500.00")
    _day(db_session, contract, date(ANNO, 3, 5))
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service.issue(invoice.id, InvoiceIssue(data_emissione=date(ANNO, 3, 31)), ADMIN)
    card.valido_a = date(ANNO, 2, 28)
    db_session.flush()

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 3, 1), date(ANNO, 3, 31)
    )

    (rate,) = analysis.per_contratto
    assert rate.tariffa_reale == Decimal("500.00")
    assert rate.tariffa_nominale is None
    assert rate.giorni_senza_listino == Decimal("1.00")


def test_a_draft_invoice_and_a_day_outside_the_period_are_not_in_the_rate(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _invoice_service(db_session, local_storage)
    contract = _contract(db_session)
    _card(db_session, contract)
    _day(db_session, contract, date(ANNO, 3, 5))
    assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)

    analysis = AnalyticsService(db_session).days_analysis(
        READER, date(ANNO, 3, 1), date(ANNO, 3, 31)
    )

    assert analysis.per_contratto == []
    assert analysis.tariffa_reale is None
    assert analysis.giorni_fatturati == Decimal("0.00")
    assert db_session.query(InvoiceLine).count() == 1


def test_a_day_that_slipped_without_approval_is_counted_in_the_month_of_the_slip(
    db_session: Session,
) -> None:
    today = today_local()
    contract = _contract(db_session, requires_prior_approval=True)
    # Asked as worked on a contract that wants prior approval, so the database redirects
    # it: the transition is what the trend counts.
    slipped = _day(db_session, contract, date(today.year, today.month, 1), "lavorato")
    assert slipped.stato == "lavorato_senza_approvazione"

    analysis = AnalyticsService(db_session).days_analysis(READER)
    assert analysis.senza_approvazione_nel_periodo == 1
    assert analysis.mesi[0].senza_approvazione == 1
    assert analysis.mesi[0].quota_senza_approvazione == 1.0
    assert analysis.senza_approvazione_sempre == 1
    assert _stack(analysis.mesi[0], "lavorato_senza_approvazione") == Decimal("1.00")


def test_one_bound_without_the_other_and_a_backwards_period_are_refused_by_name(
    db_session: Session,
) -> None:
    service = AnalyticsService(db_session)
    with pytest.raises(ValidationFailed) as only_from:
        service.days_analysis(READER, da=date(ANNO, 1, 1))
    assert only_from.value.details["field"] == "a"
    with pytest.raises(ValidationFailed) as backwards:
        service.days_analysis(READER, date(ANNO, 3, 1), date(ANNO, 1, 1))
    assert backwards.value.details["field"] == "da"
    with pytest.raises(ValidationFailed) as too_long:
        service.days_analysis(READER, date(2016, 1, 1), date(2026, 1, 31))
    assert too_long.value.details["field"] == "a"
    # Exactly ten years is the largest period admitted.
    assert len(service.days_analysis(READER, date(2016, 2, 1), date(2026, 1, 31)).mesi) == 120
