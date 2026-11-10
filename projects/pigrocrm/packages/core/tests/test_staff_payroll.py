"""What staff cost and when it leaves, against the real schema (REB-1008, spec 2026-11-09, staff,
payroll and expenses, § 2 and the C4 amendment of § 4).

The card's «done when»: an employee's month shows cost and cash estimates apart with their
dates; a collaborator sees only the aggregate «Stipendi» line; a member with no accepted
payslip has no net pay and no IRPEF line. Dates are derived from today, never literals, since
the forecasts are written from today forward.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tax_period import FIRST_OF_MONTH, TODAY

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import DueItemListQuery, PayrollTotalsQuery
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import AgentForbidden, Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.staff.estimate import add_months, first_of
from pigrocrm.core.staff.models import StaffMember
from pigrocrm.core.staff.payroll import PayrollService
from pigrocrm.core.staff.schemas import (
    DirectorCreate,
    EmployeeCreate,
    PayDayWrite,
    PayslipFigures,
    StaffMemberUpdate,
)
from pigrocrm.core.staff.service import StaffService

D = dt.date
ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
READONLY = Actor(id=uuid4(), type="user", role="readonly")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)

THIS_MONTH = first_of(TODAY)
NEXT_MONTH = add_months(THIS_MONTH, 1)
LAST_MONTH = add_months(THIS_MONTH, -1)


def _entity(session: Session, pack_id: str = "it-standard") -> UUID:
    entity = LegalEntity(name="payroll", legal_name="Payroll S.r.l.", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id=pack_id, regime_code="RF01"))
    session.flush()
    return entity.id


def _employee(entity: UUID, **overrides: object) -> EmployeeCreate:
    values: dict[str, object] = {
        "legal_entity_id": entity,
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
    }
    values.update(overrides)
    return EmployeeCreate(**values)  # type: ignore[arg-type]


def _payslip(period: dt.date = LAST_MONTH, **overrides: object) -> PayslipFigures:
    values: dict[str, object] = {
        "period": period,
        "gross": Decimal("2142.86"),
        "net": Decimal("1607.15"),
        "withholding": Decimal("321.43"),
    }
    values.update(overrides)
    return PayslipFigures(**values)  # type: ignore[arg-type]


def _world(session: Session, **employee: object) -> tuple[UUID, UUID]:
    entity = _entity(session)
    service = StaffService(session)
    service.set_payroll_pay_day(entity, PayDayWrite(pay_day=27, pay_month_offset=0), ADMIN)
    member = service.create_staff_member(_employee(entity, **employee), ADMIN)
    return entity, member.id


def _payroll_items(session: Session, **filters: object) -> list[DueItem]:
    stmt = select(DueItem).where(DueItem.source_kind == "payroll").order_by(DueItem.due_date)
    for column, value in filters.items():
        stmt = stmt.where(getattr(DueItem, column) == value)
    return list(session.execute(stmt).scalars())


def test_a_member_with_no_accepted_payslip_has_no_net_pay_no_irpef_and_no_due_items(
    db_session: Session,
) -> None:
    entity, member = _world(db_session)
    # Nothing was written for the person: the due item to them and the F24 lines both wait.
    assert _payroll_items(db_session) == []
    month = PayrollService(db_session).get_payroll_month(entity, NEXT_MONTH, ADMIN)
    (row,) = month.members
    assert row.staff_member_id == member
    assert row.source == "estimate"
    # The cost is estimated from the typed inputs alone.
    assert row.cost is not None and row.cost.total == Decimal("3275.00")
    assert row.cash == []
    assert "accepted_payslip" in row.not_estimated
    assert month.cost_total == Decimal("3275.00") and month.cash_total == Decimal("0.00")


def test_an_accepted_payslip_gives_net_pay_and_the_f24_lines_as_forecasts_in_their_months(
    db_session: Session,
) -> None:
    entity, member = _world(db_session)
    StaffService(db_session).record_staff_payslip(member, _payslip(), ADMIN)

    # The next month's estimate, cost and cash apart, each cash item with its date.
    month = PayrollService(db_session).get_payroll_month(entity, NEXT_MONTH, ADMIN)
    (row,) = month.members
    assert row.cost is not None and row.cost.total == Decimal("3275.00")
    cash = {item.kind: item for item in row.cash}
    assert cash["net_pay"].due_date == NEXT_MONTH.replace(day=27)
    assert cash["withholding"].due_date == add_months(NEXT_MONTH, 1).replace(day=16)
    assert cash["inps_contributions"].due_date == add_months(NEXT_MONTH, 1).replace(day=16)

    # The same figures are due items of the payroll source, forecasts, each to its payee.
    forecasts = {
        item.source_rule: item
        for item in _payroll_items(db_session, state="forecast")
        if item.source_rule is not None
    }

    def forecast(kind: str) -> DueItem:
        (found,) = [
            item
            for rule, item in forecasts.items()
            if rule.startswith(f"payroll_{kind}:{NEXT_MONTH:%Y-%m}:")
        ]
        return found

    net = forecast("net_pay")
    assert (net.direction, net.counterparty_type, net.staff_member_id) == (
        "out",
        "staff_member",
        UUID(str(member)),
    )
    assert net.amount == cash["net_pay"].amount and net.due_date == cash["net_pay"].due_date
    withholding = forecast("withholding")
    assert (withholding.counterparty_type, withholding.tax) == ("tax_office", "payroll_withholding")
    assert forecast("inps_contributions").tax == "payroll_inps"
    # A forecast is never in the past: only a document settles money that already left.
    assert all(item.due_date >= TODAY for item in forecasts.values())


def test_a_month_with_an_accepted_payslip_is_expected_with_its_real_figures(
    db_session: Session,
) -> None:
    _, member = _world(db_session)
    recorded = StaffService(db_session).record_staff_payslip(member, _payslip(), ADMIN)
    items = _payroll_items(db_session, source_id=recorded.id)
    # What the payslip prints is fixed; the INPS contributions are not on it and stay estimated.
    assert {(item.state, item.tax or "net_pay") for item in items} == {
        ("expected", "net_pay"),
        ("expected", "payroll_withholding"),
    }
    by = {item.tax or "net_pay": item for item in items}
    assert by["net_pay"].amount == Decimal("1607.15")
    assert by["payroll_withholding"].amount == Decimal("321.43")
    assert by["net_pay"].due_date == LAST_MONTH.replace(day=27)
    # And no forecast is left for that month.
    assert not [
        item
        for item in _payroll_items(db_session, state="forecast")
        if item.source_rule and item.source_rule.endswith(f":{LAST_MONTH:%Y-%m}")
    ]
    month = PayrollService(db_session).get_payroll_month(
        db_session.get(StaffMember, member).legal_entity_id, LAST_MONTH, ADMIN
    )
    assert month.members[0].source == "payslip"


def test_the_refresh_leaves_an_unchanged_forecast_alone_and_rewrites_a_changed_one(
    db_session: Session,
) -> None:
    _, member = _world(db_session)
    service = StaffService(db_session)
    service.record_staff_payslip(member, _payslip(), ADMIN)
    before = {i.id: i for i in _payroll_items(db_session, state="forecast")}
    assert before

    service.update_staff_member(member, StaffMemberUpdate(contract_level="4"), ADMIN)
    assert {i.id for i in _payroll_items(db_session, state="forecast")} == set(before)

    service.update_staff_member(
        member, StaffMemberUpdate(gross_annual_pay=Decimal("36000.00")), ADMIN
    )
    after = _payroll_items(db_session, state="forecast")
    assert len(after) == len(before)
    net = next(
        i for i in after if (i.source_rule or "").startswith(f"payroll_net_pay:{NEXT_MONTH:%Y-%m}:")
    )
    assert net.amount > Decimal("1900.00")
    # The replaced ones are dropped, never left to be read twice.
    assert net.id not in before
    dropped = [i for i in _payroll_items(db_session) if i.state == "dropped"]
    assert net.id not in {i.id for i in dropped} and dropped


def test_the_13th_14th_and_the_yearly_inail_are_forecast_in_their_months(
    db_session: Session,
) -> None:
    _, member = _world(db_session)
    StaffService(db_session).record_staff_payslip(member, _payslip(), ADMIN)
    rules = {i.source_rule: i for i in _payroll_items(db_session, state="forecast")}
    december = [
        r
        for r in rules
        if r and r.startswith("payroll_extra_pay:") and r.split(":")[1].endswith("-12")
    ]
    june = [
        r
        for r in rules
        if r and r.startswith("payroll_extra_pay:") and r.split(":")[1].endswith("-06")
    ]
    assert december and june
    assert all(rules[r].counterparty_type == "staff_member" for r in december + june)
    # The 13th leaves in December and the 14th in June, on the pay day's day.
    assert all(rules[r].due_date.month == 12 and rules[r].due_date.day == 27 for r in december)
    assert all(rules[r].due_date.month == 6 and rules[r].due_date.day == 27 for r in june)
    inail = [r for r in rules if r and r.startswith("payroll_inail_premium:")]
    assert inail and all(
        rules[r].due_date.month == 2 and rules[r].due_date.day == 16 for r in inail
    )
    assert all(rules[r].tax == "payroll_inail" for r in inail)


def test_a_collaborator_reads_one_stipendi_line_per_date_and_no_payroll_item(
    db_session: Session,
) -> None:
    entity, member = _world(db_session)
    other = StaffService(db_session).create_staff_member(
        _employee(entity, name="Luca Verdi", gross_annual_pay=Decimal("24000.00")), ADMIN
    )
    service = StaffService(db_session)
    service.record_staff_payslip(member, _payslip(), ADMIN)
    service.record_staff_payslip(
        other.id,
        _payslip(gross=Decimal("1714.29"), net=Decimal("1300.00"), withholding=Decimal("250.00")),
        ADMIN,
    )
    dues = DueItemService(db_session)

    # An administrator reads the items themselves.
    admin_page = dues.list(DueItemListQuery(source_kind="payroll", limit=200), ADMIN)
    assert admin_page.items
    # A collaborator's list leaves them out and naming the kind is refused.
    assert not [
        i
        for i in dues.list(DueItemListQuery(limit=200), COLLABORATOR).items
        if i.source_kind == "payroll"
    ]
    with pytest.raises(PermissionDenied):
        dues.list(DueItemListQuery(source_kind="payroll"), COLLABORATOR)
    with pytest.raises(PermissionDenied):
        dues.get(admin_page.items[0].id, COLLABORATOR)

    # What they read instead: one line per date, adding the people up, with no person behind it.
    totals = dues.list_payroll_totals(PayrollTotalsQuery(legal_entity_id=entity), COLLABORATOR)
    assert totals.items
    per_date: dict[dt.date, Decimal] = {}
    for item in admin_page.items:
        assert item.due_date is not None
        per_date[item.due_date] = per_date.get(item.due_date, Decimal(0)) + item.remaining
    assert {line.due_date: line.amount for line in totals.items} == per_date
    for line in totals.items:
        assert set(line.model_dump()) == {"label", "due_date", "amount", "forecast"}
        assert line.label == "Stipendi"
    # An administrator reads the same aggregate, and so does a readonly member.
    assert dues.list_payroll_totals(PayrollTotalsQuery(), READONLY).items == totals.items
    # An agent token reads it as it reads the other due items.
    assert dues.list_payroll_totals(PayrollTotalsQuery(), AGENT_ADMIN).items


@pytest.mark.parametrize("actor", [COLLABORATOR, READONLY], ids=["collaborator", "readonly"])
def test_a_collaborator_and_a_readonly_member_are_refused_the_estimate_and_the_payslip(
    db_session: Session, actor: Actor
) -> None:
    entity, member = _world(db_session)
    with pytest.raises(PermissionDenied):
        PayrollService(db_session).get_payroll_month(entity, THIS_MONTH, actor)
    with pytest.raises(PermissionDenied):
        StaffService(db_session).record_staff_payslip(member, _payslip(), actor)


def test_a_token_reads_the_estimate_only_with_full_access_and_never_records_a_payslip(
    db_session: Session,
) -> None:
    entity, member = _world(db_session)
    payroll = PayrollService(db_session)
    with pytest.raises(AgentForbidden):
        payroll.get_payroll_month(entity, THIS_MONTH, AGENT_ADMIN)
    assert payroll.get_payroll_month(entity, THIS_MONTH, AGENT_ADMIN_FULL).members
    # Accepting is a person's act, whatever the token carries.
    with pytest.raises(AgentForbidden):
        StaffService(db_session).record_staff_payslip(member, _payslip(), AGENT_ADMIN_FULL)


def test_a_payslip_is_one_per_month_inside_the_employment_and_its_parts_add_up(
    db_session: Session,
) -> None:
    _, member = _world(db_session, start_date=D(2026, 1, 1))
    service = StaffService(db_session)
    service.record_staff_payslip(member, _payslip(D(2026, 3, 15)), ADMIN)
    with pytest.raises(Conflict):
        service.record_staff_payslip(member, _payslip(D(2026, 3, 1)), ADMIN)
    with pytest.raises(ValidationFailed):
        service.record_staff_payslip(member, _payslip(D(2025, 12, 1)), ADMIN)
    with pytest.raises(ValueError, match="lordo"):
        PayslipFigures(
            period=D(2026, 4, 1),
            gross=Decimal("1000.00"),
            net=Decimal("800.00"),
            withholding=Decimal("300.00"),
        )


def test_a_director_has_the_net_fee_and_the_withholding_from_the_ratios_alone(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    service.set_payroll_pay_day(entity, PayDayWrite(pay_day=27, pay_month_offset=0), ADMIN)
    # `it-company` is not a pack this release ships: a company's director is built directly,
    # which is also how its law resolves to nothing and its F24 line is left out, named.
    director = StaffMember(
        legal_entity_id=entity,
        kind="director",
        name="Marco Rossi",
        start_date=D(2025, 1, 1),
        annual_fee=Decimal("24000.00"),
        fee_payment="monthly",
        accrues_tfm=False,
    )
    db_session.add(director)
    db_session.flush()
    service.record_staff_payslip(
        director.id,
        _payslip(gross=Decimal("2000.00"), net=Decimal("1500.00"), withholding=Decimal("300.00")),
        ADMIN,
    )
    (row,) = PayrollService(db_session).get_payroll_month(entity, NEXT_MONTH, ADMIN).members
    assert row.cost is not None and row.cost.total == Decimal("2000.00")
    # Net fee only: a fee's withholding and the «gestione separata» are «to verify».
    assert {i.kind for i in row.cash} == {"net_pay"}
    assert {"fee_withholding", "gestione_separata"} <= set(row.not_estimated)


def test_the_director_schema_still_holds_no_contract_input() -> None:
    with pytest.raises(ValueError):
        DirectorCreate(
            legal_entity_id=uuid4(),
            name="x",
            start_date=D(2026, 1, 1),
            annual_fee=Decimal("1000.00"),
            employee_inps_rate=Decimal("9"),  # type: ignore[call-arg]
        )


def _linked_staff(session: Session, deal_id: UUID, user_id: UUID) -> None:
    deal = session.get(Deal, deal_id)
    assert deal is not None
    session.add(
        StaffMember(
            legal_entity_id=deal.legal_entity_id,
            kind="employee",
            name="Tester",
            user_id=user_id,
            start_date=D(2025, 1, 1),
            gross_annual_pay=Decimal("30000.00"),
            monthly_payments=14,
            part_time_percent=Decimal("100"),
            employer_inps_rate=Decimal("30.00"),
            employer_inail_rate=Decimal("1.00"),
            accrues_tfm=False,
        )
    )
    session.flush()


def test_the_period_pnl_counts_labour_once_for_a_linked_user_and_only_for_an_admin(
    db_session: Session, open_deal_with_hours: UUID, seeded_user_id: UUID
) -> None:
    analytics = AnalyticsService(db_session)
    query = PeriodPnlQuery(date_from=FIRST_OF_MONTH, date_to=TODAY)
    before = analytics.period_pnl(query, ADMIN)
    # The deal's own hours (10h at 40.00) are in its labour while nobody is on the payroll.
    assert before.in_progress.work_cost == Decimal("400.00")
    assert before.personnel == Decimal("0.00")

    _linked_staff(db_session, open_deal_with_hours, seeded_user_id)
    admin = analytics.period_pnl(query, ADMIN)
    # The staff member's cost enters as the personnel line, a row of its own, and the same
    # hours leave the deal's labour: counted once.
    assert admin.in_progress.work_cost == Decimal("0.00")
    days = (TODAY - FIRST_OF_MONTH).days + 1
    month_days = (add_months(THIS_MONTH, 1) - THIS_MONTH).days
    assert admin.personnel == (Decimal("3275.00") * days / month_days).quantize(Decimal("0.01"))

    # Whoever may not read pay gets no personnel line and the P&L as it always was.
    collaborator = analytics.period_pnl(query, COLLABORATOR)
    assert collaborator.personnel is None
    assert collaborator.in_progress.work_cost == Decimal("400.00")
    # A space with the module off has none either.
    assert analytics.period_pnl(query, ADMIN, frozenset({"time", "deals"})).personnel is None
    # A token without full access is not an administrator for pay.
    assert analytics.period_pnl(query, AGENT_ADMIN).personnel is None


def test_an_accepted_payslip_is_the_months_personnel_cost(
    db_session: Session, open_deal_with_hours: UUID, seeded_user_id: UUID
) -> None:
    _linked_staff(db_session, open_deal_with_hours, seeded_user_id)
    member = db_session.scalars(select(StaffMember)).one()
    StaffService(db_session).record_staff_payslip(
        member.id,
        _payslip(
            THIS_MONTH,
            gross=Decimal("3100.00"),
            net=Decimal("2300.00"),
            withholding=Decimal("500.00"),
        ),
        ADMIN,
    )
    window = PeriodPnlQuery(
        date_from=THIS_MONTH, date_to=add_months(THIS_MONTH, 1) - dt.timedelta(days=1)
    )
    personnel = AnalyticsService(db_session).period_pnl(window, ADMIN).personnel
    # The payslip's gross and the employer's 30% and 1% on it, the whole month.
    assert personnel == Decimal("4061.00")


def test_an_expected_item_an_administrator_dropped_is_not_written_again(
    db_session: Session,
) -> None:
    _, member = _world(db_session)
    recorded = StaffService(db_session).record_staff_payslip(member, _payslip(), ADMIN)
    expected = _payroll_items(db_session, source_id=recorded.id)
    assert len(expected) == 2
    DueItemService(db_session).drop(expected[0].id, "pagato fuori dal sistema")
    StaffService(db_session).update_staff_member(
        member, StaffMemberUpdate(contract_level="5"), ADMIN
    )
    assert len(_payroll_items(db_session, source_id=recorded.id)) == 2


def test_a_forecast_whose_date_passed_is_not_in_the_aggregate(db_session: Session) -> None:
    entity, member = _world(db_session)
    StaffService(db_session).record_staff_payslip(member, _payslip(), ADMIN)
    old = _payroll_items(db_session, state="forecast")[0]
    old.own_date = old.due_date = TODAY - dt.timedelta(days=3)
    db_session.flush()
    lines = DueItemService(db_session).list_payroll_totals(
        PayrollTotalsQuery(legal_entity_id=entity), COLLABORATOR
    )
    assert TODAY - dt.timedelta(days=3) not in {line.due_date for line in lines.items}


def test_the_daily_pass_rolls_the_payroll_window_and_a_failing_entity_does_not_stop_it(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pigrocrm.core.automations.daily import AutomationPass

    entity, member = _world(db_session)
    StaffService(db_session).record_staff_payslip(member, _payslip(), ADMIN)
    # The window has run out: every forecast is gone, as after a year of nobody editing staff.
    for item in _payroll_items(db_session, state="forecast"):
        DueItemService(db_session).drop(item.id, "finestra scaduta")
    assert not _payroll_items(db_session, state="forecast")

    report = AutomationPass(db_session).run()
    assert report.payroll_failed == 0 and report.payroll_refreshed >= 1
    assert _payroll_items(db_session, state="forecast")

    other, _ = _world(db_session)
    real = PayrollService.refresh_payroll_forecasts

    def flaky(self: PayrollService, legal_entity_id: UUID) -> None:
        if legal_entity_id == other:
            raise RuntimeError("boom")
        real(self, legal_entity_id)

    monkeypatch.setattr(PayrollService, "refresh_payroll_forecasts", flaky)
    again = AutomationPass(db_session).run()
    assert again.payroll_failed == 1 and again.payroll_refreshed >= 1
