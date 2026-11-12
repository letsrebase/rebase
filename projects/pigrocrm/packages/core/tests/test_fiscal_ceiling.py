"""The ceiling engine and the expense-recovery invoice line, against real invoice rows --
REB-361, REB-344 §8-9, REB-352 §2/§6.

No `InvoiceService` here on purpose: what these tests exercise is the arithmetic
against rows that already satisfy the schema's own constraints, the same way
`fakes/invoice_fixtures.py` builds an issued invoice as a row rather than through an
emission. The full create-issue-export cycle, including the expense-recovery line surviving
`issue()`'s own natura/riferimento_normativo consistency check and a real FatturaPA
export, is `test_invoice_expense-recovery_line.py`.
"""

from datetime import date
from decimal import Decimal

from fakes.customer_fixtures import make_customer
from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.ceiling import (
    evaluate_ceiling,
    evaluate_pack,
    paid_revenue_for_calendar_year,
    social_security_surcharge_line_for_contract,
)
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK, SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID
from pigrocrm.core.invoices.models import Invoice, InvoiceLine

REVENUE_CEILING = IT_FLAT_RATE_PACK.ceilings[0]
EXIT_CEILING = IT_FLAT_RATE_PACK.ceilings[1]
assert REVENUE_CEILING.id == "revenue_ceiling"
assert EXIT_CEILING.id == "immediate_exit_ceiling"

YEAR = 2024


def _invoice(
    session: Session,
    *,
    number: int,
    year: int,
    lines: list[tuple[str, Decimal]],
    payment_status: str = "paid",
    payment_date: date | None,
) -> Invoice:
    """An `issued` `invoice` built as a row, with `lines` as `(descrizione,
    prezzo_totale)` pairs, each a zero-rate `N2.2` line -- the shape every line under
    the forfettario already carries (`fiscal/regime.py::_flat_rate`)."""
    taxable_amount = sum((price for _, price in lines), start=Decimal("0.00"))
    invoice = Invoice(
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=year,
        number=number,
        issue_date=date(year, 6, 15),
        taxable_amount=taxable_amount,
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=taxable_amount,
        payment_status=payment_status,
        payment_date=payment_date,
    )
    session.add(invoice)
    session.flush()
    for i, (description, price) in enumerate(lines, start=1):
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=i,
                description=description,
                quantity=Decimal("1.000000"),
                unit_price=price,
                total_price=price,
                vat_rate=Decimal("0.00"),
                vat_nature="N2.2",
            )
        )
    session.flush()
    return invoice


def _paid(session: Session, *, number: int, year: int, lines: list[tuple[str, Decimal]]) -> Invoice:
    return _invoice(session, number=number, year=year, lines=lines, payment_date=date(year, 6, 20))


# --- evaluate_ceiling: the pure half, no session ------------------------------------


def test_no_alert_below_the_lowest_ratio() -> None:
    status = evaluate_ceiling(REVENUE_CEILING, Decimal("50000.00"))
    assert status.alert_level is None
    assert status.exceeded is False
    assert status.remaining == Decimal("35000.00")


def test_approaching_at_the_lower_alert_ratio() -> None:
    status = evaluate_ceiling(REVENUE_CEILING, Decimal("68000.00"))  # 0.8 * 85000
    assert status.alert_level == "In avvicinamento alla soglia di ricavi"
    assert status.exceeded is False


def test_reached_at_the_threshold_itself() -> None:
    status = evaluate_ceiling(REVENUE_CEILING, Decimal("85000.00"))
    assert status.alert_level == "Soglia di ricavi raggiunta"
    assert status.exceeded is True
    assert status.remaining == Decimal("0.00")


def test_the_immediate_exit_ceiling_uses_its_own_higher_alert_ratio() -> None:
    below = evaluate_ceiling(EXIT_CEILING, Decimal("89999.99"))
    at_ratio = evaluate_ceiling(EXIT_CEILING, Decimal("90000.00"))  # 0.9 * 100000
    assert below.alert_level is None
    assert at_ratio.alert_level == ("In avvicinamento alla fuoriuscita immediata dal regime")


def test_a_revenue_over_the_hard_ceiling_breaches_both() -> None:
    assert evaluate_ceiling(REVENUE_CEILING, Decimal("120000.00")).exceeded is True
    assert evaluate_ceiling(EXIT_CEILING, Decimal("120000.00")).exceeded is True


# --- paid_revenue_for_calendar_year / evaluate_pack: real rows ---------------------


def test_paid_revenue_excludes_unpaid_invoices_and_other_years(db_session: Session) -> None:
    _paid(db_session, number=1, year=YEAR, lines=[("Consulenza", Decimal("1000.00"))])
    _invoice(
        db_session,
        number=2,
        year=YEAR,
        lines=[("Consulenza", Decimal("5000.00"))],
        payment_status="unpaid",
        payment_date=None,
    )
    _paid(db_session, number=1, year=YEAR + 1, lines=[("Consulenza", Decimal("2000.00"))])

    assert paid_revenue_for_calendar_year(db_session, YEAR) == Decimal("1000.00")


def test_paid_revenue_falls_back_to_issue_date_when_payment_date_is_unset(
    db_session: Session,
) -> None:
    """`stato_pagamento = 'incassato'` with no `payment_date` recorded still counts,
    read by `issue_date` -- the same fallback `AnalyticsRepository.monthly_paid`
    already uses, mirrored here rather than reinvented."""
    _invoice(
        db_session,
        number=1,
        year=YEAR,
        lines=[("Consulenza", Decimal("300.00"))],
        payment_status="paid",
        payment_date=None,
    )
    assert paid_revenue_for_calendar_year(db_session, YEAR) == Decimal("300.00")


def test_the_calendar_year_is_a_half_open_range_on_the_collection_date(
    db_session: Session,
) -> None:
    """The first and the last day of the year count, the day either side does not, and
    the year is read off `payment_date`, not off the invoice's own `year`."""
    for number, collected in enumerate(
        (date(YEAR - 1, 12, 31), date(YEAR, 1, 1), date(YEAR, 12, 31), date(YEAR + 1, 1, 1)),
        start=1,
    ):
        _invoice(
            db_session,
            number=number,
            year=YEAR,
            lines=[("Consulenza", Decimal("100.00"))],
            payment_date=collected,
        )
    assert paid_revenue_for_calendar_year(db_session, YEAR) == Decimal("200.00")


def test_evaluate_pack_evaluates_both_ceilings_off_one_paid_revenue_figure(
    db_session: Session,
) -> None:
    _paid(db_session, number=1, year=YEAR, lines=[("Consulenza", Decimal("90000.00"))])
    statuses = {s.ceiling.id: s for s in evaluate_pack(IT_FLAT_RATE_PACK, db_session, YEAR)}

    assert statuses["revenue_ceiling"].exceeded is True
    assert statuses["immediate_exit_ceiling"].exceeded is False
    assert statuses["immediate_exit_ceiling"].alert_level == (
        "In avvicinamento alla fuoriuscita immediata dal regime"
    )


def test_to_social_security_surcharge_line_counts_toward_the_paid_revenue_in_full(
    db_session: Session,
) -> None:
    """REB-352 §2's own "no code change is needed for the ceiling side" -- a expense-recovery
    line is one more line inside `Invoice.taxable_amount`, no filtering needed."""
    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2026)
    _paid(
        db_session,
        number=1,
        year=YEAR,
        lines=[("Consulenza", Decimal("1000.00")), (charge.description_line, Decimal("40.00"))],
    )
    assert paid_revenue_for_calendar_year(db_session, YEAR) == Decimal("1040.00")


# --- expense-recovery_line_for_contract: pure, no session ------------------------------------


def test_no_line_is_produced_without_the_contracts_election() -> None:
    assert (
        social_security_surcharge_line_for_contract(
            IT_FLAT_RATE_PACK,
            applies_social_charge=False,
            fee_subtotal=Decimal("1000.00"),
            year=2026,
        )
        is None
    )


def test_the_line_is_four_percent_of_the_fee_subtotal() -> None:
    line = social_security_surcharge_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=Decimal("1000.00"), year=2026
    )
    assert line is not None
    assert line.unit_price == Decimal("40.00")
    assert line.quantity == Decimal("1.000000")
    assert line.vat_rate == Decimal("0.00")
    assert (
        line.description
        == IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2026).description_line
    )


def test_the_line_rounds_half_up_like_every_other_money_figure() -> None:
    # 4% of 12.345 = 0.4938 -> 0.49.
    line = social_security_surcharge_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=Decimal("12.345"), year=2026
    )
    assert line is not None
    assert line.unit_price == Decimal("0.49")


def test_to_zero_fee_subtotal_still_produces_to_zero_social_security_surcharge_line() -> None:
    """`None` means "not elected"; an elected contract with nothing invoiced yet is a
    real, zero-amount line, not the same thing as no election at all."""
    line = social_security_surcharge_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=Decimal("0.00"), year=2026
    )
    assert line is not None
    assert line.unit_price == Decimal("0.00")


# --- the surcharge line is collected revenue, one figure for the ceiling and the coefficient ---


def test_the_surcharge_line_counts_in_the_collected_revenue_in_full(
    db_session: Session,
) -> None:
    """REB-1100: the 4% is a collected fee like any other, so the one revenue figure the
    ceiling reads and the coefficient is applied to holds fee and surcharge together."""
    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2026)
    _paid(
        db_session,
        number=1,
        year=YEAR,
        lines=[("Consulenza", Decimal("1000.00")), (charge.description_line, Decimal("40.00"))],
    )
    assert paid_revenue_for_calendar_year(db_session, YEAR) == Decimal("1040.00")


def test_a_part_payment_collects_the_same_share_of_the_surcharge_as_of_the_fee(
    db_session: Session,
) -> None:
    """A payment counts for its share of the taxable amount, surcharge line included: half
    the invoice paid in the year is half of fee and surcharge together."""
    from pigrocrm.core.actor import Actor
    from pigrocrm.core.due_items.sales import SALES_INVOICE
    from pigrocrm.core.due_items.schemas import SettlementCreate
    from pigrocrm.core.due_items.service import DueItemService

    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2026)
    invoice = _invoice(
        db_session,
        number=1,
        year=YEAR,
        lines=[("Consulenza", Decimal("1000.00")), (charge.description_line, Decimal("40.00"))],
        payment_status="unpaid",
        payment_date=None,
    )
    service = DueItemService(db_session)
    item = service.repo.by_source(SALES_INVOICE, invoice.id)
    assert item is not None
    service.settle(
        item.id,
        SettlementCreate(amount=Decimal("520.00"), settled_on=date(YEAR, 7, 1)),
        Actor(id=None, type="system", role="admin"),
    )
    assert paid_revenue_for_calendar_year(db_session, YEAR) == Decimal("520.00")
