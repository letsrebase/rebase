"""The year as cash (`AnalyticsService.cash_overview`) and the economic overview that
lays the fiscal estimate over it."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fakes.customer_fixtures import make_customer
from orologio import TODAY_IN_ITALY, congela
from sqlalchemy import text
from sqlalchemy.orm import Session
from tax_period import TODAY

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
COLLABORATOR = Actor(id=None, type="mcp", role="collaborator")


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
        .create_cost_category(CostCategoryCreate(nome=f"Cassa test {TODAY.isoformat()}"), ADMIN)
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
        invoice_id, PaymentState(payment_status="paid", payment_date=TODAY), ADMIN
    )
    CostService(db_session).create(
        CostCreate(
            category_id=cost_category_id,
            data=TODAY,
            amount=Decimal("120.00"),
            description="Hosting",
        ),
        ADMIN,
    )

    overview = AnalyticsService(db_session).cash_overview(TODAY.year, COLLABORATOR)
    assert overview.year == TODAY.year
    assert len(overview.months) == 12
    month = next(m for m in overview.months if m.month == TODAY.month)
    assert month.paid == Decimal(str(paid.total))
    assert month.costs == Decimal("120.00")
    assert month.unpaid == Decimal("0.00")
    # Shares of the tallest stack: both present, and the stack adds up to the whole.
    assert month.trend_shares["paid"] > 0 and month.trend_shares["costs"] > 0
    assert abs(month.trend_shares["paid"] + month.trend_shares["costs"] - 1.0) < 1e-9
    # The height of each stacked column as money (ORB-139), summed here, once, because
    # the browser never turns an amount into a number. Costs are inside it, which is why
    # it is a `pila` and not a total: the chart prints it above the bar a reader measures.
    assert month.trend_stack == month.paid + Decimal("120.00")
    assert month.projection_stack == month.paid + Decimal("120.00")
    empty = next(m for m in overview.months if m.month != TODAY.month)
    assert empty.trend_stack == Decimal("0.00")
    assert empty.projection_stack == Decimal("0.00")
    assert sum(m.paid for m in overview.months) == overview.paid
    assert overview.projected == overview.paid  # nothing pending, nothing drafted
    assert overview.actual_gross == overview.paid - Decimal("120.00")
    assert overview.projected_gross == overview.actual_gross


def test_an_issued_unpaid_invoice_is_projected_not_collected(
    db_session: Session, issued_invoice_line_id: UUID
) -> None:
    invoice_id = _invoice_of(db_session, issued_invoice_line_id)
    total = db_session.execute(
        text("select totale from invoices where id = :id"), {"id": str(invoice_id)}
    ).scalar_one()
    overview = AnalyticsService(db_session).cash_overview(TODAY.year, COLLABORATOR)
    assert overview.paid == Decimal("0.00")
    assert overview.unpaid == Decimal(str(total))
    assert overview.projected == Decimal(str(total))
    # Owed money is projected, not collected: it is in the projection's column total
    # and not in the cash chart's.
    month = next(m for m in overview.months if m.unpaid > Decimal("0.00"))
    assert month.projection_stack == Decimal(str(total))
    assert month.trend_stack == Decimal("0.00")


def test_a_draft_counts_as_a_draft_and_a_paid_invoice_never_twice(
    db_session: Session, draft_invoice_line_id: UUID
) -> None:
    overview = AnalyticsService(db_session).cash_overview(TODAY.year, COLLABORATOR)
    assert overview.drafts > Decimal("0.00")
    assert overview.paid == Decimal("0.00")
    assert overview.unpaid == Decimal("0.00")
    # A draft is in the projection's stack and not in the cash chart's: the two heights
    # differ by exactly the two projection-only series.
    month = next(m for m in overview.months if m.drafts > Decimal("0.00"))
    assert month.projection_stack == month.drafts
    assert month.trend_stack == Decimal("0.00")


def test_a_proforma_is_projected_in_the_month_of_its_own_date(
    db_session: Session, local_storage: LocalFileStorage, draft_invoice_line_id: UUID
) -> None:
    """ORB-63 gives a proforma a document date, and `monthly_drafts` buckets by it: a
    proforma the sender dated in another month is that month's projected money, whatever
    day it was typed in. The fattura draft beside it has no date until `issue` and stays
    in the month it was created, which is this one."""
    from datetime import date
    from decimal import Decimal as D

    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn

    draft_id = _invoice_of(db_session, draft_invoice_line_id)
    service = InvoiceService(db_session, local_storage)
    customer_id = service.get(draft_id, ADMIN).customer_id
    other_month = 1 if TODAY.month != 1 else 2
    service.create(
        InvoiceCreate(
            customer_id=customer_id,
            type="proforma",
            issue_date=date(TODAY.year, other_month, 15),
            lines=[InvoiceLineIn(description="Consulenza", unit_price=D("333.00"))],
        ),
        ADMIN,
    )
    overview = AnalyticsService(db_session).cash_overview(TODAY.year, COLLABORATOR)
    by_month = {m.month: m.drafts for m in overview.months}
    assert by_month[other_month] == D("333.00")
    assert by_month[TODAY.month] == overview.drafts - D("333.00")
    assert by_month[TODAY.month] > D("0.00")


def test_the_overview_keeps_the_estimate_from_everyone_but_an_admin_with_a_profile(
    db_session: Session,
) -> None:
    service = AnalyticsService(db_session)
    assert service.economic_overview(TODAY.year, COLLABORATOR).tax is None
    # An admin without a fiscal profile: cash, and no estimate rather than an error.
    without = service.economic_overview(TODAY.year, ADMIN)
    assert without.tax is None and without.actual_net is None


def test_the_estimate_follows_collected_and_projected_revenue(
    db_session: Session, issued_invoice_line_id: UUID
) -> None:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(
            regime_code="RF19",
            profitability_coefficient=Decimal("67.00"),
            substitute_tax_rate=Decimal("5.00"),
            social_security_rate=Decimal("26.07"),
        ),
        ADMIN,
    )
    overview = AnalyticsService(db_session).economic_overview(TODAY.year, ADMIN)
    assert overview.tax is not None and overview.projected_tax is not None
    # Nothing collected yet: the actual estimate is on zero, the projected one on the
    # issued invoice, and the two nets follow.
    assert overview.tax.revenue == Decimal("0.00")
    assert overview.projected_tax.revenue == overview.pension_fund.projected > Decimal("0.00")
    assert overview.projected_tax.due_total == (
        overview.projected_tax.substitute_tax + overview.projected_tax.contributions
    )
    assert overview.projected_net == (
        overview.pension_fund.projected_gross - overview.projected_tax.due_total
    )
    assert overview.actual_net == overview.pension_fund.actual_gross - overview.tax.due_total


# --- the two readings of the cash view: by accrual period, or by the money (ORB-133) ---


def _issued_for_period(
    db_session: Session,
    local_storage: LocalFileStorage,
    customer_id: UUID,
    *,
    accrual_month: int,
    price: str,
    type: str = "invoice",
) -> UUID:
    """A document dated today whose declared accrual period is another month of the year:
    the one shape under which the two readings of the cash view visibly disagree."""
    from datetime import date, timedelta

    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn

    service = InvoiceService(db_session, local_storage)
    first = date(TODAY.year, accrual_month, 1)
    last = date(TODAY.year, accrual_month + 1, 1) - timedelta(days=1)
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            type=type,
            issue_date=TODAY if type == "proforma" else None,
            accrual_from=first,
            accrual_to=last,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal(price))],
        ),
        ADMIN,
    )
    if type == "invoice":
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
    other_month = 1 if TODAY.month != 1 else 2

    paid_id = _issued_for_period(
        db_session, local_storage, customer_id, accrual_month=other_month, price="1000.00"
    )
    service.set_payment_state(
        paid_id, PaymentState(payment_status="paid", payment_date=TODAY), ADMIN
    )
    unpaid_id = _issued_for_period(
        db_session, local_storage, customer_id, accrual_month=other_month, price="200.00"
    )
    # `issue` stamps a due date from the fiscal profile's payment terms, so by the money
    # the unpaid invoice sits in the month it falls due, wherever that is.
    due_date = service.get(unpaid_id, ADMIN).due_date
    assert due_date is not None
    _issued_for_period(
        db_session,
        local_storage,
        customer_id,
        accrual_month=other_month,
        price="30.00",
        type="proforma",
    )
    # The fixture's own draft has no period and no date: it stays in the month it was
    # created under both readings, which is this one.
    draft_today = Decimal("100.00")

    analytics = AnalyticsService(db_session)

    accrual = analytics.cash_overview(TODAY.year, COLLABORATOR)
    assert accrual.base == "accrual"
    declared = next(m for m in accrual.months if m.month == other_month)
    today = next(m for m in accrual.months if m.month == TODAY.month)
    assert declared.paid == Decimal("1000.00")
    assert declared.unpaid == Decimal("200.00")
    assert declared.drafts == Decimal("30.00")
    assert today.paid == Decimal("0.00")
    assert today.unpaid == Decimal("0.00")
    assert today.drafts == draft_today

    collection = analytics.cash_overview(TODAY.year, COLLABORATOR, base="collection")
    assert collection.base == "collection"
    declared = next(m for m in collection.months if m.month == other_month)
    today = next(m for m in collection.months if m.month == TODAY.month)
    assert declared.paid == declared.drafts == Decimal("0.00")
    assert today.paid == Decimal("1000.00")
    assert today.drafts == Decimal("30.00") + draft_today
    by_month = {m.month: m.unpaid for m in collection.months}
    if due_date.year == TODAY.year:
        assert by_month[due_date.month] == Decimal("200.00")
        assert declared.unpaid == (
            Decimal("200.00") if due_date.month == other_month else Decimal("0.00")
        )
    else:
        # Due next year: by the money it is not this year's projection at all.
        assert sum(by_month.values()) == Decimal("0.00")

    # In this fixture the paid and the drafted money add up the same under both readings,
    # since every date involved is in this year: only the month moves. The unpaid invoice
    # is the one that can leave the year, when it falls due in the next one.
    assert accrual.paid == collection.paid
    assert accrual.drafts == collection.drafts


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
            regime_code="RF19",
            profitability_coefficient=Decimal("67.00"),
            substitute_tax_rate=Decimal("5.00"),
            social_security_rate=Decimal("26.07"),
        ),
        ADMIN,
    )
    service = InvoiceService(db_session, local_storage)
    customer_id = service.get(_invoice_of(db_session, draft_invoice_line_id), ADMIN).customer_id
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            accrual_from=date(TODAY.year - 1, 12, 1),
            accrual_to=date(TODAY.year - 1, 12, 31),
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    service.issue(invoice.id, InvoiceIssue(), ADMIN)
    service.set_payment_state(
        invoice.id, PaymentState(payment_status="paid", payment_date=TODAY), ADMIN
    )

    # The same year boundary for the other two series: a proforma dated today for
    # December's work, and an unpaid invoice for it. `monthly_drafts` is the one with the
    # three-way fallback (period, document date, creation day), so it is the one to pin.
    service.create(
        InvoiceCreate(
            customer_id=customer_id,
            type="proforma",
            issue_date=TODAY,
            accrual_from=date(TODAY.year - 1, 12, 1),
            accrual_to=date(TODAY.year - 1, 12, 31),
            lines=[InvoiceLineIn(description="Saldo", unit_price=Decimal("40.00"))],
        ),
        ADMIN,
    )
    unpaid = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            accrual_from=date(TODAY.year - 1, 12, 1),
            accrual_to=date(TODAY.year - 1, 12, 31),
            lines=[InvoiceLineIn(description="Saldo", unit_price=Decimal("500.00"))],
        ),
        ADMIN,
    )
    service.issue(unpaid.id, InvoiceIssue(), ADMIN)
    due_date = service.get(unpaid.id, ADMIN).due_date
    assert due_date is not None

    analytics = AnalyticsService(db_session)
    by_accrual = analytics.economic_overview(TODAY.year, ADMIN)
    by_cash = analytics.economic_overview(TODAY.year, ADMIN, base="collection")

    # The charts disagree: December's work is last year's by accrual, this year's by cash.
    # The fixture's own dateless draft (100.00) is this year's under both.
    assert by_accrual.pension_fund.base == "accrual"
    assert by_accrual.pension_fund.paid == Decimal("0.00")
    assert by_cash.pension_fund.paid == Decimal("1000.00")
    assert by_accrual.pension_fund.drafts == Decimal("100.00")
    assert by_cash.pension_fund.drafts == Decimal("140.00")
    assert by_accrual.pension_fund.unpaid == Decimal("0.00")
    assert by_cash.pension_fund.unpaid == (
        Decimal("500.00") if due_date.year == TODAY.year else Decimal("0.00")
    )
    # The tax block does not: it is the same estimate on the same collected revenue.
    assert by_accrual.tax is not None and by_cash.tax is not None
    assert by_accrual.tax.revenue == by_cash.tax.revenue == Decimal("1000.00")
    assert by_accrual.tax.due_total == by_cash.tax.due_total
    assert by_accrual.actual_net == by_cash.actual_net
    assert by_accrual.projected_tax is not None and by_cash.projected_tax is not None
    assert by_accrual.projected_tax.revenue == by_cash.projected_tax.revenue
    assert by_accrual.projected_net == by_cash.projected_net


# --- REB-352 §1.6: contract-date markers on the cash calendar -----------------------


def _contract(db_session: Session, customer: Customer, **overrides: object) -> Contract:
    payload: dict[str, object] = {
        "customer_id": customer.id,
        "title": "Consulenza CTO",
        "start": date(2025, 6, 1),
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def test_cash_overview_carries_an_irrevocability_marker_for_an_open_ended_contract(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serving notice "today" (frozen at `TODAY_IN_ITALY`) still runs the contract's
    own 30-day notice period, so the window closes 30 days out -- regardless of
    which `year` is on screen, since the notice period is measured from today."""
    congela(monkeypatch)
    customer = make_customer(db_session)
    contract = _contract(db_session, customer, termination_notice_days=30, end=None)

    overview = AnalyticsService(db_session).cash_overview(TODAY_IN_ITALY.year, COLLABORATOR)

    markers = [m for m in overview.contractual_due_dates if m.contract_id == contract.id]
    assert len(markers) == 1
    assert markers[0].type == "irrevocability_end"
    assert markers[0].data == date(2026, 1, 31)
    assert markers[0].customer_id == customer.id
    assert markers[0].title == "Consulenza CTO"


def test_cash_overview_clips_the_irrevocability_marker_to_the_contracts_own_end(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = make_customer(db_session)
    contract = _contract(db_session, customer, termination_notice_days=90, end=date(2026, 1, 10))

    overview = AnalyticsService(db_session).cash_overview(TODAY_IN_ITALY.year, COLLABORATOR)

    markers = [m for m in overview.contractual_due_dates if m.contract_id == contract.id]
    assert [m.data for m in markers if m.type == "irrevocability_end"] == [date(2026, 1, 10)]


def test_cash_overview_carries_a_renewal_deadline_marker(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = make_customer(db_session)
    contract = _contract(
        db_session,
        customer,
        renewal_type="tacit",
        renewal_notice_days=60,
        end=date(2026, 6, 1),
    )

    overview = AnalyticsService(db_session).cash_overview(TODAY_IN_ITALY.year, COLLABORATOR)

    markers = [
        m
        for m in overview.contractual_due_dates
        if m.contract_id == contract.id and m.type == "renewal_due"
    ]
    assert [m.data for m in markers] == [date(2026, 4, 2)]


def test_cash_overview_has_no_renewal_marker_for_a_contract_with_no_renewal_clause(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = make_customer(db_session)
    contract = _contract(db_session, customer, renewal_type="none", end=date(2026, 6, 1))

    overview = AnalyticsService(db_session).cash_overview(TODAY_IN_ITALY.year, COLLABORATOR)

    assert not [
        m
        for m in overview.contractual_due_dates
        if m.contract_id == contract.id and m.type == "renewal_due"
    ]


def test_cash_overview_excludes_a_marker_falling_outside_the_queried_year(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`termination_notice_days=400` pushes the window past `TODAY_IN_ITALY`'s own
    year: absent from that year's calendar, present the moment the calendar reaches
    the year the window actually falls in."""
    congela(monkeypatch)
    customer = make_customer(db_session)
    contract = _contract(db_session, customer, termination_notice_days=400, end=None)

    this_year = AnalyticsService(db_session).cash_overview(TODAY_IN_ITALY.year, COLLABORATOR)
    next_year = AnalyticsService(db_session).cash_overview(TODAY_IN_ITALY.year + 1, COLLABORATOR)

    assert not [m for m in this_year.contractual_due_dates if m.contract_id == contract.id]
    markers = [m for m in next_year.contractual_due_dates if m.contract_id == contract.id]
    assert [m.data for m in markers] == [date(2027, 2, 5)]


def test_cash_overview_excludes_a_marker_for_a_soft_deleted_contract(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    congela(monkeypatch)
    customer = make_customer(db_session)
    contract = _contract(db_session, customer, termination_notice_days=1, end=None)
    contract.deleted_at = datetime.now(UTC)
    db_session.flush()

    overview = AnalyticsService(db_session).cash_overview(TODAY_IN_ITALY.year, COLLABORATOR)

    assert not [m for m in overview.contractual_due_dates if m.contract_id == contract.id]
