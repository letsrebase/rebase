"""The monthly estimate of a staff member's cost and cash (REB-1008, spec 2026-11-09, staff,
payroll and expenses, § 2), on the pure function: no database.

What the card promises: cost and cash apart with their dates; the 13th and 14th payments in
their months; net pay and the income-tax withholding only from the last accepted payslip's
ratios, so a member with none has neither and no F24 line of any kind; a part whose input is
missing is named and never defaulted.
"""

import datetime as dt
from decimal import Decimal
from uuid import uuid4

from pigrocrm.core.fiscal.pack import ITALIAN_PAYROLL
from pigrocrm.core.staff.estimate import MonthEstimate, PayDay, Ratios, estimate_month
from pigrocrm.core.staff.models import StaffMember, StaffPayslip

D = dt.date
PAY_DAY = PayDay(day=27, month_offset=0)
RATIOS = Ratios(net_to_gross=Decimal("0.75"), withholding_to_gross=Decimal("0.15"))
MARCH = D(2026, 3, 1)


def _employee(**overrides: object) -> StaffMember:
    values: dict[str, object] = {
        "id": uuid4(),
        "legal_entity_id": uuid4(),
        "kind": "employee",
        "name": "Giulia Bianchi",
        "start_date": D(2025, 1, 1),
        "gross_annual_pay": Decimal("30000.00"),
        "monthly_payments": 14,
        "part_time_percent": Decimal("100"),
        "employer_inps_rate": Decimal("30.00"),
        "employee_inps_rate": Decimal("9.19"),
        "employer_inail_rate": Decimal("1.00"),
        "thirteenth_month": 12,
        "fourteenth_month": 6,
        "accrues_tfm": False,
    }
    values.update(overrides)
    return StaffMember(**values)


def _director(**overrides: object) -> StaffMember:
    values: dict[str, object] = {
        "id": uuid4(),
        "legal_entity_id": uuid4(),
        "kind": "director",
        "name": "Marco Rossi",
        "start_date": D(2025, 1, 1),
        "annual_fee": Decimal("24000.00"),
        "fee_payment": "monthly",
        "accrues_tfm": False,
    }
    values.update(overrides)
    return StaffMember(**values)


def _estimate(
    member: StaffMember,
    month: dt.date = MARCH,
    *,
    ratios: Ratios | None = RATIOS,
    pay_day: PayDay | None = PAY_DAY,
    payslip: StaffPayslip | None = None,
) -> MonthEstimate:
    return estimate_month(
        member, month, pay_day=pay_day, law=ITALIAN_PAYROLL, ratios=ratios, payslip=payslip
    )


def _cash(estimate: MonthEstimate) -> dict[str, tuple[Decimal, dt.date]]:
    return {item.kind: (item.amount, item.due_date) for item in estimate.cash}


def test_an_employees_month_has_a_cost_and_a_cash_estimate_apart_with_their_dates() -> None:
    estimate = _estimate(_employee())
    # Cost: 30,000 a year over twelve months, plus the employer's INPS (30%) and INAIL (1%).
    assert estimate.cost is not None
    assert estimate.cost.gross == Decimal("2500.00")
    assert estimate.cost.employer_inps == Decimal("750.00")
    assert estimate.cost.employer_inail == Decimal("25.00")
    assert estimate.cost.total == Decimal("3275.00")
    # Cash: a payment is a fourteenth of the year, 2,142.86; each part has its own date.
    cash = _cash(estimate)
    assert cash["net_pay"] == (Decimal("1607.15"), D(2026, 3, 27))
    # Withheld on pay, paid the 16th of the month after the payment.
    assert cash["withholding"] == (Decimal("321.43"), D(2026, 4, 16))
    # The employer's 30% and the employee's 9.19% together, on the 16th of the month after the
    # month worked.
    assert cash["inps_contributions"] == (Decimal("839.79"), D(2026, 4, 16))
    assert set(cash) == {"net_pay", "withholding", "inps_contributions"}
    assert estimate.source == "estimate"
    assert estimate.not_estimated == ()


def test_the_pay_day_in_the_month_after_moves_the_net_pay_and_the_withholding_with_it() -> None:
    cash = _cash(_estimate(_employee(), pay_day=PayDay(day=10, month_offset=1)))
    assert cash["net_pay"][1] == D(2026, 4, 10)
    assert cash["withholding"][1] == D(2026, 5, 16)
    # The contributions follow the month worked, not the pay day.
    assert cash["inps_contributions"][1] == D(2026, 4, 16)


def test_the_13th_and_14th_payments_leave_in_their_months_and_are_not_a_cost_of_them() -> None:
    member = _employee()
    december = _estimate(member, D(2026, 12, 1))
    june = _estimate(member, D(2026, 6, 1))
    for estimate in (december, june):
        cash = _cash(estimate)
        assert cash["extra_pay"] == (
            Decimal("1607.15"),
            D(estimate.month.year, estimate.month.month, 27),
        )
        # The regular pay and the extra one are withheld together.
        assert cash["withholding"][0] == Decimal("642.86")
        # They accrue in every month: the cost of the month is the same as any other's.
        assert estimate.cost is not None and estimate.cost.total == Decimal("3275.00")
    assert "extra_pay" not in _cash(_estimate(member, D(2026, 7, 1)))


def test_the_13th_leaves_in_its_own_month_even_when_the_regular_pay_is_the_month_after() -> None:
    member = _employee()
    december = _cash(_estimate(member, D(2026, 12, 1), pay_day=PayDay(day=10, month_offset=1)))
    # The regular pay of December is paid on 10 January, the 13th on 10 December (`extra_pay`
    # is dated in the month typed for it); each is withheld the 16th after its own payment.
    assert december["net_pay"][1] == D(2027, 1, 10)
    assert december["extra_pay"][1] == D(2026, 12, 10)
    withholdings = sorted(
        (i.due_date, i.amount)
        for i in _estimate(member, D(2026, 12, 1), pay_day=PayDay(10, 1)).cash
        if i.kind == "withholding"
    )
    assert [due for due, _ in withholdings] == [D(2027, 1, 16), D(2027, 2, 16)]
    assert [amount for _, amount in withholdings] == [Decimal("321.43"), Decimal("321.43")]
    # The contributions follow the month the work was done, for both.
    assert december["inps_contributions"][1] == D(2027, 1, 16)


def test_a_thirteen_payment_contract_has_no_14th_and_a_payment_with_no_month_is_named() -> None:
    thirteen = _employee(monthly_payments=13, fourteenth_month=None)
    assert "extra_pay" not in _cash(_estimate(thirteen, D(2026, 6, 1)))
    assert "extra_pay" in _cash(_estimate(thirteen, D(2026, 12, 1)))
    # Each regular payment is a thirteenth of the year now.
    assert _cash(_estimate(thirteen))["net_pay"][0] == Decimal("1730.77")

    unsaid = _employee(thirteenth_month=None, fourteenth_month=None)
    estimate = _estimate(unsaid, D(2026, 12, 1))
    assert "extra_pay" not in _cash(estimate)
    assert "thirteenth_month" in estimate.not_estimated
    assert "fourteenth_month" in estimate.not_estimated


def test_an_extra_payment_matures_over_the_twelve_months_that_end_with_it() -> None:
    # Hired on 1 October: a quarter of the year's months before December's 13th.
    member = _employee(
        start_date=D(2026, 10, 1), thirteenth_month=12, fourteenth_month=None, monthly_payments=13
    )
    cash = _cash(_estimate(member, D(2026, 12, 1)))
    # 92 days of the 365 of the window: a 13th of 30,000 is 2,307.69.
    assert cash["extra_pay"][0] == round_net(Decimal("2307.69") * Decimal(92) / Decimal(365))


def round_net(gross: Decimal) -> Decimal:
    return (gross * Decimal("0.75")).quantize(Decimal("0.01"))


def test_a_member_with_no_accepted_payslip_has_no_net_pay_no_withholding_and_no_f24() -> None:
    member = _employee()
    for month in (MARCH, D(2026, 2, 1), D(2026, 12, 1)):
        estimate = _estimate(member, month, ratios=None)
        # The cost needs only the typed inputs.
        assert estimate.cost is not None and estimate.cost.total == Decimal("3275.00")
        # Nothing leaves: not net pay, not IRPEF, and not the INPS and INAIL lines either, so no
        # partial F24 forecast appears, in the month INAIL falls in too.
        assert estimate.cash == ()
        assert "accepted_payslip" in estimate.not_estimated


def test_the_f24_lines_come_together_or_not_at_all() -> None:
    # Without the employee's INPS share the INPS line cannot be written, and then neither is
    # the withholding: an F24 with one of them would be partial. The net pay stands.
    estimate = _estimate(_employee(employee_inps_rate=None))
    assert set(_cash(estimate)) == {"net_pay"}
    assert "employee_inps_rate" in estimate.not_estimated

    # Nor without the INAIL rate: its premium is paid in the same F24 as the month's.
    no_inail = _estimate(_employee(employer_inail_rate=None))
    assert set(_cash(no_inail)) == {"net_pay"}
    assert "employer_inail_rate" in no_inail.not_estimated

    no_pay_day = _estimate(_employee(), pay_day=None)
    assert no_pay_day.cash == ()
    assert "pay_day" in no_pay_day.not_estimated

    no_law = estimate_month(_employee(), MARCH, pay_day=PAY_DAY, law=None, ratios=RATIOS)
    assert set(_cash(no_law)) == {"net_pay"}
    assert "payroll_law" in no_law.not_estimated


def test_a_rate_that_was_not_typed_is_named_and_never_defaulted() -> None:
    estimate = _estimate(_employee(employer_inps_rate=None, employer_inail_rate=None))
    assert estimate.cost is not None
    assert estimate.cost.employer_inps is None and estimate.cost.employer_inail is None
    assert estimate.cost.total == Decimal("2500.00")
    assert "employer_inps_rate" in estimate.not_estimated
    assert "employer_inail_rate" in estimate.not_estimated
    assert "inps_contributions" not in _cash(estimate)


def test_the_part_time_share_scales_the_pay() -> None:
    estimate = _estimate(_employee(part_time_percent=Decimal("50")))
    assert estimate.cost is not None and estimate.cost.gross == Decimal("1250.00")


def test_a_month_part_in_service_counts_by_calendar_days() -> None:
    # Hired on the 16th of March: 16 of its 31 days.
    estimate = _estimate(_employee(start_date=D(2026, 3, 16)))
    assert estimate.cost is not None and estimate.cost.gross == Decimal("1290.32")
    # A month before the start has nothing to say, and says nothing of what is missing either.
    before = _estimate(_employee(start_date=D(2026, 3, 16)), D(2026, 2, 1))
    assert before.empty and before.not_estimated == ()
    # Nor one after the end.
    after = _estimate(_employee(end_date=D(2026, 2, 28)), MARCH)
    assert after.empty


def test_inail_is_one_yearly_settlement_dated_in_february() -> None:
    estimate = _estimate(_employee(), D(2026, 2, 1))
    # The advance of 2026 on 30,000 at 1%; last year's regularisation is nothing, the advance
    # February 2025 took having been a full year's.
    assert _cash(estimate)["inail_premium"] == (Decimal("300.00"), D(2026, 2, 16))
    assert "inail_premium" not in _cash(_estimate(_employee(), D(2026, 3, 1)))

    # Hired in June 2025: no advance in February 2025, so February 2026 settles 2025's actual
    # pay (214 of 365 days) and advances 2026's.
    hired = _estimate(_employee(start_date=D(2025, 6, 1)), D(2026, 2, 1))
    assert _cash(hired)["inail_premium"][0] == Decimal("475.89")

    # Left in the middle of 2025 after a full-year advance: a credit, still in February.
    left = _estimate(_employee(end_date=D(2025, 6, 30)), D(2026, 2, 1))
    credit = _cash(left)["inail_premium"][0]
    assert credit < 0
    assert left.cost is None


def test_a_directors_cost_and_net_fee_come_from_the_fee_and_the_ratios_with_no_f24() -> None:
    estimate = _estimate(_director())
    assert estimate.cost is not None
    assert estimate.cost.total == Decimal("2000.00")
    # The net fee leaves on the pay day; nothing is withheld to the tax office or paid to the INPS
    # in an estimate, because what a fee is withheld at and the «gestione separata» are «to
    # verify» (A5): they are said, not computed.
    assert _cash(estimate) == {"net_pay": (Decimal("1500.00"), D(2026, 3, 27))}
    assert "fee_withholding" in estimate.not_estimated
    assert "gestione_separata" in estimate.not_estimated

    no_payslip = _estimate(_director(), ratios=None)
    assert no_payslip.cash == ()


def test_a_fee_not_paid_monthly_has_cost_but_no_schedule() -> None:
    estimate = _estimate(_director(fee_payment="other"))
    assert estimate.cost is not None and estimate.cost.total == Decimal("2000.00")
    assert estimate.cash == ()
    assert "fee_schedule" in estimate.not_estimated


def test_an_accepted_payslip_replaces_the_estimate_of_its_month() -> None:
    payslip = StaffPayslip(
        staff_member_id=uuid4(),
        legal_entity_id=uuid4(),
        period=MARCH,
        gross=Decimal("2300.00"),
        net=Decimal("1700.00"),
        withholding=Decimal("350.00"),
    )
    estimate = _estimate(_employee(), payslip=payslip)
    assert estimate.source == "payslip"
    assert estimate.cost is not None
    assert estimate.cost.gross == Decimal("2300.00")
    assert estimate.cost.employer_inps == Decimal("690.00")
    cash = _cash(estimate)
    assert cash["net_pay"] == (Decimal("1700.00"), D(2026, 3, 27))
    assert cash["withholding"] == (Decimal("350.00"), D(2026, 4, 16))
    # The contributions still come from the typed rates, on the payslip's gross.
    assert cash["inps_contributions"][0] == Decimal("901.37")
    assert "extra_pay" not in cash
