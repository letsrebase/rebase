"""The year as cash (`AnalyticsService.cash_overview`) and the economic overview that
lays the fiscal estimate over it."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from orologio import OGGI_IN_ITALIA, congela
from periodo_fiscale import OGGI
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.invoices.schemas import PaymentState
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.schemas import CostCreate

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATORE = Actor(id=None, type="mcp", role="collaboratore")


def _invoice_of(session: Session, line_id: UUID) -> UUID:
    invoice_id: UUID = session.execute(
        text("SELECT invoice_id FROM invoice_lines WHERE id = :line"), {"line": line_id}
    ).scalar_one()
    return invoice_id


@pytest.fixture
def cost_category_id(db_session: Session) -> UUID:
    from pigrocrm.core.timetracking.categories import CostCategoryService
    from pigrocrm.core.timetracking.schemas import CostCategoryCreate

    return (
        CostCategoryService(db_session)
        .create_cost_category(CostCategoryCreate(nome=f"Cassa test {OGGI.isoformat()}"), ADMIN)
        .id
    )


def test_the_year_adds_up_month_by_month_and_costs_are_not_revenue(
    db_session: Session,
    local_storage: LocalFileStorage,
    issued_invoice_line_id: UUID,
    cost_category_id: UUID,
) -> None:
    # The fixture already provisioned the fiscal and emitter profiles the service needs.
    service = InvoiceService(db_session, local_storage)
    invoice_id = _invoice_of(db_session, issued_invoice_line_id)
    paid = service.set_payment_state(
        invoice_id, PaymentState(stato_pagamento="incassato", data_incasso=OGGI), ADMIN
    )
    CostService(db_session).create(
        CostCreate(
            category_id=cost_category_id,
            data=OGGI,
            importo=Decimal("120.00"),
            descrizione="Hosting",
        ),
        ADMIN,
    )

    overview = AnalyticsService(db_session).cash_overview(OGGI.year, COLLABORATORE)
    assert overview.anno == OGGI.year
    assert len(overview.mesi) == 12
    month = next(m for m in overview.mesi if m.mese == OGGI.month)
    assert month.incassato == Decimal(str(paid.totale))
    assert month.costi == Decimal("120.00")
    assert month.da_incassare == Decimal("0.00")
    # Shares of the tallest stack: both present, and the stack adds up to the whole.
    assert month.quote_andamento["incassato"] > 0 and month.quote_andamento["costi"] > 0
    assert abs(month.quote_andamento["incassato"] + month.quote_andamento["costi"] - 1.0) < 1e-9
    # The height of each stacked column as money (ORB-139), summed here, once, because
    # the browser never turns an amount into a number. Costs are inside it, which is why
    # it is a `pila` and not a total: the chart prints it above the bar a reader measures.
    assert month.pila_andamento == month.incassato + Decimal("120.00")
    assert month.pila_proiezione == month.incassato + Decimal("120.00")
    empty = next(m for m in overview.mesi if m.mese != OGGI.month)
    assert empty.pila_andamento == Decimal("0.00")
    assert empty.pila_proiezione == Decimal("0.00")
    assert sum(m.incassato for m in overview.mesi) == overview.incassato
    assert overview.proiettato == overview.incassato  # nothing pending, nothing drafted
    assert overview.lordo_effettivo == overview.incassato - Decimal("120.00")
    assert overview.lordo_proiettato == overview.lordo_effettivo


def test_an_issued_unpaid_invoice_is_projected_not_collected(
    db_session: Session, issued_invoice_line_id: UUID
) -> None:
    invoice_id = _invoice_of(db_session, issued_invoice_line_id)
    totale = db_session.execute(
        text("select totale from invoices where id = :id"), {"id": str(invoice_id)}
    ).scalar_one()
    overview = AnalyticsService(db_session).cash_overview(OGGI.year, COLLABORATORE)
    assert overview.incassato == Decimal("0.00")
    assert overview.da_incassare == Decimal(str(totale))
    assert overview.proiettato == Decimal(str(totale))
    # Owed money is projected, not collected: it is in the projection's column total
    # and not in the cash chart's.
    month = next(m for m in overview.mesi if m.da_incassare > Decimal("0.00"))
    assert month.pila_proiezione == Decimal(str(totale))
    assert month.pila_andamento == Decimal("0.00")


def test_a_draft_counts_as_a_draft_and_a_paid_invoice_never_twice(
    db_session: Session, draft_invoice_line_id: UUID
) -> None:
    overview = AnalyticsService(db_session).cash_overview(OGGI.year, COLLABORATORE)
    assert overview.bozze > Decimal("0.00")
    assert overview.incassato == Decimal("0.00")
    assert overview.da_incassare == Decimal("0.00")
    # A draft is in the projection's stack and not in the cash chart's: the two heights
    # differ by exactly the two projection-only series.
    month = next(m for m in overview.mesi if m.bozze > Decimal("0.00"))
    assert month.pila_proiezione == month.bozze
    assert month.pila_andamento == Decimal("0.00")


def test_a_proforma_is_projected_in_the_month_of_its_own_date(
    db_session: Session, local_storage: LocalFileStorage, draft_invoice_line_id: UUID
) -> None:
    """ORB-63 gives a proforma a document date, and `monthly_bozze` buckets by it: a
    proforma the sender dated in another month is that month's projected money, whatever
    day it was typed in. The fattura draft beside it has no date until `issue` and stays
    in the month it was created, which is this one."""
    from datetime import date
    from decimal import Decimal as D

    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn

    draft_id = _invoice_of(db_session, draft_invoice_line_id)
    service = InvoiceService(db_session, local_storage)
    customer_id = service.get(draft_id, ADMIN).customer_id
    altro_mese = 1 if OGGI.month != 1 else 2
    service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            data_emissione=date(OGGI.year, altro_mese, 15),
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=D("333.00"))],
        ),
        ADMIN,
    )
    overview = AnalyticsService(db_session).cash_overview(OGGI.year, COLLABORATORE)
    by_month = {m.mese: m.bozze for m in overview.mesi}
    assert by_month[altro_mese] == D("333.00")
    assert by_month[OGGI.month] == overview.bozze - D("333.00")
    assert by_month[OGGI.month] > D("0.00")


def test_the_overview_keeps_the_estimate_from_everyone_but_an_admin_with_a_profile(
    db_session: Session,
) -> None:
    service = AnalyticsService(db_session)
    assert service.economic_overview(OGGI.year, COLLABORATORE).fiscale is None
    # An admin without a fiscal profile: cash, and no estimate rather than an error.
    without = service.economic_overview(OGGI.year, ADMIN)
    assert without.fiscale is None and without.netto_effettivo is None


def test_the_estimate_follows_collected_and_projected_revenue(
    db_session: Session, issued_invoice_line_id: UUID
) -> None:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(
            codice_regime="RF19",
            coefficiente_redditivita=Decimal("67.00"),
            aliquota_imposta_sostitutiva=Decimal("5.00"),
            aliquota_inps=Decimal("26.07"),
        ),
        ADMIN,
    )
    overview = AnalyticsService(db_session).economic_overview(OGGI.year, ADMIN)
    assert overview.fiscale is not None and overview.fiscale_proiettato is not None
    # Nothing collected yet: the actual estimate is on zero, the projected one on the
    # issued invoice, and the two nets follow.
    assert overview.fiscale.ricavi == Decimal("0.00")
    assert overview.fiscale_proiettato.ricavi == overview.cassa.proiettato > Decimal("0.00")
    assert overview.fiscale_proiettato.totale_dovuto == (
        overview.fiscale_proiettato.imposta_sostitutiva + overview.fiscale_proiettato.contributi
    )
    assert overview.netto_proiettato == (
        overview.cassa.lordo_proiettato - overview.fiscale_proiettato.totale_dovuto
    )
    assert (
        overview.netto_effettivo == overview.cassa.lordo_effettivo - overview.fiscale.totale_dovuto
    )


# --- the two readings of the cash view: by accrual period, or by the money (ORB-133) ---


def _issued_for_period(
    db_session: Session,
    local_storage: LocalFileStorage,
    customer_id: UUID,
    *,
    competenza_mese: int,
    prezzo: str,
    tipo: str = "fattura",
) -> UUID:
    """A document dated today whose declared accrual period is another month of the year:
    the one shape under which the two readings of the cash view visibly disagree."""
    from datetime import date, timedelta

    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn

    service = InvoiceService(db_session, local_storage)
    primo = date(OGGI.year, competenza_mese, 1)
    ultimo = date(OGGI.year, competenza_mese + 1, 1) - timedelta(days=1)
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo=tipo,
            data_emissione=OGGI if tipo == "proforma" else None,
            competenza_da=primo,
            competenza_a=ultimo,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal(prezzo))],
        ),
        ADMIN,
    )
    if tipo == "fattura":
        service.issue(invoice.id, InvoiceIssue(), ADMIN)
    return invoice.id


def test_the_cash_view_reads_by_accrual_period_by_default_and_by_the_money_on_request(
    db_session: Session, local_storage: LocalFileStorage, draft_invoice_line_id: UUID
) -> None:
    """Ivan, 2026-09-10: «deve essere per mese di competenza non incassato o emissione
    della fattura», then a switch with competenza as the default. Three documents, all
    dated today, all declaring another month as their period: a paid invoice, an unpaid
    one and a proforma. By accrual all three sit in the declared month; by the money the
    paid one sits where it was collected, the unpaid one where it falls due (its issue
    date, here) and the proforma on its own document date, exactly as before this card."""
    service = InvoiceService(db_session, local_storage)
    customer_id = service.get(_invoice_of(db_session, draft_invoice_line_id), ADMIN).customer_id
    altro_mese = 1 if OGGI.month != 1 else 2

    paid_id = _issued_for_period(
        db_session, local_storage, customer_id, competenza_mese=altro_mese, prezzo="1000.00"
    )
    service.set_payment_state(
        paid_id, PaymentState(stato_pagamento="incassato", data_incasso=OGGI), ADMIN
    )
    unpaid_id = _issued_for_period(
        db_session, local_storage, customer_id, competenza_mese=altro_mese, prezzo="200.00"
    )
    # `issue` stamps a due date from the fiscal profile's payment terms, so by the money
    # the unpaid invoice sits in the month it falls due, wherever that is.
    scadenza = service.get(unpaid_id, ADMIN).data_scadenza
    assert scadenza is not None
    _issued_for_period(
        db_session,
        local_storage,
        customer_id,
        competenza_mese=altro_mese,
        prezzo="30.00",
        tipo="proforma",
    )
    # The fixture's own draft has no period and no date: it stays in the month it was
    # created under both readings, which is this one.
    bozza_oggi = Decimal("100.00")

    analytics = AnalyticsService(db_session)

    competenza = analytics.cash_overview(OGGI.year, COLLABORATORE)
    assert competenza.base == "competenza"
    declared = next(m for m in competenza.mesi if m.mese == altro_mese)
    today = next(m for m in competenza.mesi if m.mese == OGGI.month)
    assert declared.incassato == Decimal("1000.00")
    assert declared.da_incassare == Decimal("200.00")
    assert declared.bozze == Decimal("30.00")
    assert today.incassato == Decimal("0.00")
    assert today.da_incassare == Decimal("0.00")
    assert today.bozze == bozza_oggi

    incasso = analytics.cash_overview(OGGI.year, COLLABORATORE, base="incasso")
    assert incasso.base == "incasso"
    declared = next(m for m in incasso.mesi if m.mese == altro_mese)
    today = next(m for m in incasso.mesi if m.mese == OGGI.month)
    assert declared.incassato == declared.bozze == Decimal("0.00")
    assert today.incassato == Decimal("1000.00")
    assert today.bozze == Decimal("30.00") + bozza_oggi
    by_month = {m.mese: m.da_incassare for m in incasso.mesi}
    if scadenza.year == OGGI.year:
        assert by_month[scadenza.month] == Decimal("200.00")
        assert declared.da_incassare == (
            Decimal("200.00") if scadenza.month == altro_mese else Decimal("0.00")
        )
    else:
        # Due next year: by the money it is not this year's projection at all.
        assert sum(by_month.values()) == Decimal("0.00")

    # In this fixture the paid and the drafted money add up the same under both readings,
    # since every date involved is in this year: only the month moves. The unpaid invoice
    # is the one that can leave the year, when it falls due in the next one.
    assert competenza.incassato == incasso.incassato
    assert competenza.bozze == incasso.bozze


def test_the_estimate_stays_on_the_money_whatever_the_charts_read_by(
    db_session: Session, local_storage: LocalFileStorage, draft_invoice_line_id: UUID
) -> None:
    """The forfettario is taxed on what was collected in the calendar year, so the fiscal
    block of the overview is computed on the cash reading even when the charts read by
    accrual period (`docs/design/DECISIONS.md`, 2026-09-10). Proven with the one document
    whose two readings fall in different years: collected today, for December's work."""
    from datetime import date

    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn

    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(
            codice_regime="RF19",
            coefficiente_redditivita=Decimal("67.00"),
            aliquota_imposta_sostitutiva=Decimal("5.00"),
            aliquota_inps=Decimal("26.07"),
        ),
        ADMIN,
    )
    service = InvoiceService(db_session, local_storage)
    customer_id = service.get(_invoice_of(db_session, draft_invoice_line_id), ADMIN).customer_id
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            competenza_da=date(OGGI.year - 1, 12, 1),
            competenza_a=date(OGGI.year - 1, 12, 31),
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    service.issue(invoice.id, InvoiceIssue(), ADMIN)
    service.set_payment_state(
        invoice.id, PaymentState(stato_pagamento="incassato", data_incasso=OGGI), ADMIN
    )

    # The same year boundary for the other two series: a proforma dated today for
    # December's work, and an unpaid invoice for it. `monthly_bozze` is the one with the
    # three-way fallback (period, document date, creation day), so it is the one to pin.
    service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            data_emissione=OGGI,
            competenza_da=date(OGGI.year - 1, 12, 1),
            competenza_a=date(OGGI.year - 1, 12, 31),
            righe=[InvoiceLineIn(descrizione="Saldo", prezzo_unitario=Decimal("40.00"))],
        ),
        ADMIN,
    )
    unpaid = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            competenza_da=date(OGGI.year - 1, 12, 1),
            competenza_a=date(OGGI.year - 1, 12, 31),
            righe=[InvoiceLineIn(descrizione="Saldo", prezzo_unitario=Decimal("500.00"))],
        ),
        ADMIN,
    )
    service.issue(unpaid.id, InvoiceIssue(), ADMIN)
    scadenza = service.get(unpaid.id, ADMIN).data_scadenza
    assert scadenza is not None

    analytics = AnalyticsService(db_session)
    by_accrual = analytics.economic_overview(OGGI.year, ADMIN)
    by_cash = analytics.economic_overview(OGGI.year, ADMIN, base="incasso")

    # The charts disagree: December's work is last year's by accrual, this year's by cash.
    # The fixture's own dateless draft (100.00) is this year's under both.
    assert by_accrual.cassa.base == "competenza"
    assert by_accrual.cassa.incassato == Decimal("0.00")
    assert by_cash.cassa.incassato == Decimal("1000.00")
    assert by_accrual.cassa.bozze == Decimal("100.00")
    assert by_cash.cassa.bozze == Decimal("140.00")
    assert by_accrual.cassa.da_incassare == Decimal("0.00")
    assert by_cash.cassa.da_incassare == (
        Decimal("500.00") if scadenza.year == OGGI.year else Decimal("0.00")
    )
    # The tax block does not: it is the same estimate on the same collected revenue.
    assert by_accrual.fiscale is not None and by_cash.fiscale is not None
    assert by_accrual.fiscale.ricavi == by_cash.fiscale.ricavi == Decimal("1000.00")
    assert by_accrual.fiscale.totale_dovuto == by_cash.fiscale.totale_dovuto
    assert by_accrual.netto_effettivo == by_cash.netto_effettivo
    assert by_accrual.fiscale_proiettato is not None and by_cash.fiscale_proiettato is not None
    assert by_accrual.fiscale_proiettato.ricavi == by_cash.fiscale_proiettato.ricavi
    assert by_accrual.netto_proiettato == by_cash.netto_proiettato


# --- REB-352 §1.6: contract-date markers on the cash calendar -----------------------


def _customer(db_session: Session) -> Customer:
    customer = Customer(ragione_sociale="ACME S.r.l.")
    db_session.add(customer)
    db_session.flush()
    return customer


def _contract(db_session: Session, customer: Customer, **overrides: object) -> Contract:
    payload: dict[str, object] = {
        "customer_id": customer.id,
        "titolo": "Consulenza CTO",
        "inizio": date(2025, 6, 1),
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"tipo": "non_rimborsabile"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def test_cash_overview_carries_an_irrevocability_marker_for_an_open_ended_contract(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serving notice "today" (frozen at `OGGI_IN_ITALIA`) still runs the contract's
    own 30-day notice period, so the window closes 30 days out -- regardless of
    which `anno` is on screen, since the notice period is measured from today."""
    congela(monkeypatch)
    customer = _customer(db_session)
    contract = _contract(db_session, customer, preavviso_disdetta_giorni=30, fine=None)

    overview = AnalyticsService(db_session).cash_overview(OGGI_IN_ITALIA.year, COLLABORATORE)

    markers = [m for m in overview.scadenze_contrattuali if m.contract_id == contract.id]
    assert len(markers) == 1
    assert markers[0].tipo == "fine_irrevocabilita"
    assert markers[0].data == date(2026, 1, 31)
    assert markers[0].customer_id == customer.id
    assert markers[0].titolo == "Consulenza CTO"


def test_cash_overview_clips_the_irrevocability_marker_to_the_contracts_own_end(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = _customer(db_session)
    contract = _contract(db_session, customer, preavviso_disdetta_giorni=90, fine=date(2026, 1, 10))

    overview = AnalyticsService(db_session).cash_overview(OGGI_IN_ITALIA.year, COLLABORATORE)

    markers = [m for m in overview.scadenze_contrattuali if m.contract_id == contract.id]
    assert [m.data for m in markers if m.tipo == "fine_irrevocabilita"] == [date(2026, 1, 10)]


def test_cash_overview_carries_a_renewal_deadline_marker(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = _customer(db_session)
    contract = _contract(
        db_session,
        customer,
        tipo_rinnovo="tacito",
        preavviso_rinnovo_giorni=60,
        fine=date(2026, 6, 1),
    )

    overview = AnalyticsService(db_session).cash_overview(OGGI_IN_ITALIA.year, COLLABORATORE)

    markers = [
        m
        for m in overview.scadenze_contrattuali
        if m.contract_id == contract.id and m.tipo == "scadenza_rinnovo"
    ]
    assert [m.data for m in markers] == [date(2026, 4, 2)]


def test_cash_overview_has_no_renewal_marker_for_a_contract_with_no_renewal_clause(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = _customer(db_session)
    contract = _contract(db_session, customer, tipo_rinnovo="nessuno", fine=date(2026, 6, 1))

    overview = AnalyticsService(db_session).cash_overview(OGGI_IN_ITALIA.year, COLLABORATORE)

    assert not [
        m
        for m in overview.scadenze_contrattuali
        if m.contract_id == contract.id and m.tipo == "scadenza_rinnovo"
    ]


def test_cash_overview_excludes_a_marker_falling_outside_the_queried_year(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`preavviso_disdetta_giorni=400` pushes the window past `OGGI_IN_ITALIA`'s own
    year: absent from that year's calendar, present the moment the calendar reaches
    the year the window actually falls in."""
    congela(monkeypatch)
    customer = _customer(db_session)
    contract = _contract(db_session, customer, preavviso_disdetta_giorni=400, fine=None)

    this_year = AnalyticsService(db_session).cash_overview(OGGI_IN_ITALIA.year, COLLABORATORE)
    next_year = AnalyticsService(db_session).cash_overview(OGGI_IN_ITALIA.year + 1, COLLABORATORE)

    assert not [m for m in this_year.scadenze_contrattuali if m.contract_id == contract.id]
    markers = [m for m in next_year.scadenze_contrattuali if m.contract_id == contract.id]
    assert [m.data for m in markers] == [date(2027, 2, 5)]


def test_cash_overview_excludes_a_marker_for_a_soft_deleted_contract(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = _customer(db_session)
    contract = _contract(db_session, customer, preavviso_disdetta_giorni=1, fine=None)
    contract.deleted_at = datetime.now(UTC)
    db_session.flush()

    overview = AnalyticsService(db_session).cash_overview(OGGI_IN_ITALIA.year, COLLABORATORE)

    assert not [m for m in overview.scadenze_contrattuali if m.contract_id == contract.id]
