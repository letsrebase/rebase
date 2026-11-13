"""The live report of an engagement's days and its monthly «Rendiconto» (REB-1002, collaborators
design § 3 «The report» and § 4 «What makes a day payable»): a day is payable only in a month a
person approved or whose tacit date passed, and not held; an hourly letter pays hours; no agent
credential records or approves a month.
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import test_engagements as door_tests
from fakes.customer_fixtures import make_customer
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_engagements import Door, _frozen, _log, _seed, _space

import pigrocrm.core.collaborators.days_report as days_report
from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.days_report import DaysReportService, report_windows
from pigrocrm.core.collaborators.monthly_reports import MonthlyReportService
from pigrocrm.core.collaborators.schemas import (
    EngagementCreate,
    EngagementDaysReport,
    EngagementRead,
    MonthlyReportApproval,
    MonthlyReportRequest,
)
from pigrocrm.core.collaborators.service import EngagementService
from pigrocrm.core.config import Settings
from pigrocrm.core.engagements.schemas import (
    EngagementReport,
    ReportDeal,
    ReportEntry,
    ReportWorkUnit,
)
from pigrocrm.core.engagements.service import SpaceUnreachable
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.work_units.schemas import WorkUnitCreate
from pigrocrm.core.work_units.service import WorkUnitService

# The door's own fixture: a registry and the spaces it opens, dropped on the way out.
door = door_tests.door
D = dt.date
ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
# After the end of October and of its seven working days, with November still running.
NOW = dt.datetime(2026, 11, 20, 10, 0, tzinfo=dt.UTC)


def _settings(engine: Engine | None = None, **overrides: Any) -> Settings:
    base = {"leads_space": "rebase", "root_slug": "rebase", "public_url": "https://pigro.test"}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _engagement(
    session: Session,
    *,
    fee_unit: str = "day",
    state: str = "active",
    email: str | None = None,
    **overrides: Any,
) -> EngagementRead:
    who = make_customer(
        session,
        roles=["freelancer"],
        hub_freelancer_id=uuid4(),
        **({"email": email} if email else {}),
    )
    values: dict[str, Any] = {
        "freelancer_id": who.id,
        "role": "Backend developer",
        "starts_on": D(2026, 10, 1),
        "ends_on": D(2026, 12, 31),
        "days_per_week": Decimal("3"),
        "fee": Decimal("400.00"),
        "fee_unit": fee_unit,
        "payment_rule": "fixed_term",
        "payment_days": 30,
    }
    values.update(overrides)
    service = EngagementService(session)
    made = service.create(EngagementCreate(**values), ADMIN)
    if state in ("active", "concluded"):
        service.transition(made.id, "awaiting_signature", ADMIN)
        made = service.transition(made.id, "active", ADMIN)
    if state == "concluded":
        made = service.transition(made.id, "concluded", ADMIN, ended_on=D(2026, 11, 10))
    return made


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _frozen(monkeypatch, NOW)


# ---- the rules ------------------------------------------------------------------------------


def test_the_tacit_date_is_seven_working_days_after_receipt() -> None:
    # Tuesday 3 November: Wed, Thu, Fri, Mon, Tue, Wed, Thu.
    assert rules.tacit_date(D(2026, 11, 3)) == D(2026, 11, 12)
    # Friday 4 December: the weekend and Tuesday 8 December (Immacolata) are skipped.
    assert rules.tacit_date(D(2026, 12, 4)) == D(2026, 12, 16)
    # Received on Sunday 1 November (Ognissanti too), the count starts on the Monday.
    assert rules.tacit_date(D(2026, 11, 1)) == D(2026, 11, 10)


def test_a_report_counts_as_approved_the_day_after_its_tacit_date() -> None:
    tacit = D(2026, 11, 12)
    assert rules.effective_state("requested", tacit, tacit) == "requested"
    assert rules.effective_state("requested", tacit, tacit + dt.timedelta(days=1)) == "tacit"
    assert rules.effective_state("approved", tacit, tacit - dt.timedelta(days=5)) == "approved"
    assert rules.counts_as_approved("tacit") and rules.counts_as_approved("approved")
    assert not rules.counts_as_approved("requested")


def test_a_month_is_written_year_dash_month() -> None:
    assert rules.parse_month("2026-10") == D(2026, 10, 1)
    assert rules.month_label(D(2026, 10, 1)) == "2026-10"
    for bad in ("2026", "2026-13", "ottobre"):
        with pytest.raises(ValidationFailed):
            rules.parse_month(bad)


def test_a_long_period_is_read_in_windows_of_800_days_that_touch() -> None:
    assert report_windows(D(2026, 1, 1), D(2026, 3, 1)) == [(D(2026, 1, 1), D(2026, 3, 1))]
    windows = report_windows(D(2020, 1, 1), D(2026, 1, 1))
    assert windows[0] == (D(2020, 1, 1), D(2020, 1, 1) + dt.timedelta(days=800))
    assert windows[1][0] == windows[0][1] + dt.timedelta(days=1)
    assert windows[-1][1] == D(2026, 1, 1)
    assert all((end - start).days <= 800 for start, end in windows)


# ---- recording and approving a month ---------------------------------------------------------


def test_a_person_records_a_month_and_the_tacit_date_follows(db_session: Session) -> None:
    engagement = _engagement(db_session)
    made = MonthlyReportService(db_session, _settings()).request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 3)), ADMIN
    )
    assert (made.label, made.state, made.effective_state) == ("2026-10", "requested", "tacit")
    assert (made.received_on, made.tacit_on) == (D(2026, 11, 3), D(2026, 11, 12))
    assert made.counts_as_approved is True  # the clock is on the 20th: nobody objected

    with pytest.raises(Conflict, match="gia' registrato"):
        MonthlyReportService(db_session, _settings()).request(
            engagement.id, MonthlyReportRequest(month="2026-10"), ADMIN
        )
    page = MonthlyReportService(db_session, _settings()).for_engagement(engagement.id, AGENT)
    assert [item.label for item in page.items] == ["2026-10"]


def test_the_month_a_report_covers_must_be_over_and_inside_the_engagement(
    db_session: Session,
) -> None:
    engagement = _engagement(db_session)
    service = MonthlyReportService(db_session, _settings())
    with pytest.raises(ValidationFailed, match="non e' ancora finito"):
        service.request(engagement.id, MonthlyReportRequest(month="2026-11"), ADMIN)
    with pytest.raises(ValidationFailed, match="fuori dalle date"):
        service.request(engagement.id, MonthlyReportRequest(month="2026-09"), ADMIN)
    with pytest.raises(ValidationFailed, match="nel futuro"):
        service.request(
            engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 21)), ADMIN
        )
    with pytest.raises(ValidationFailed, match="dopo la fine"):
        service.request(
            engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 10, 31)), ADMIN
        )
    draft = _engagement(db_session, state="draft")
    with pytest.raises(Conflict, match="attivo o concluso"):
        service.request(draft.id, MonthlyReportRequest(month="2026-10"), ADMIN)
    with pytest.raises(NotFound):
        service.request(uuid4(), MonthlyReportRequest(month="2026-10"), ADMIN)


def test_recording_and_approving_a_month_are_refused_outside_the_leads_space(
    db_session: Session,
) -> None:
    engagement = _engagement(db_session)
    for elsewhere in (
        MonthlyReportService(db_session, _settings(), space_slug="ada"),
        MonthlyReportService(db_session, _settings(leads_space="")),
    ):
        with pytest.raises(Conflict, match="PIGROCRM_LEADS_SPACE"):
            elsewhere.request(engagement.id, MonthlyReportRequest(month="2026-10"), ADMIN)
        with pytest.raises(Conflict, match="PIGROCRM_LEADS_SPACE"):
            elsewhere.approve(engagement.id, "2026-10", MonthlyReportApproval(), ADMIN)
    # An agent is still told the act is a person's, before the space is looked at.
    with pytest.raises(AgentForbidden):
        MonthlyReportService(db_session, _settings(), space_slug="ada").request(
            engagement.id, MonthlyReportRequest(month="2026-10"), AGENT
        )
    assert MonthlyReportService(db_session, _settings()).request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )


def test_an_agent_token_cannot_record_or_approve_a_month(db_session: Session) -> None:
    engagement = _engagement(db_session)
    service = MonthlyReportService(db_session, _settings())
    for agent in (AGENT, AGENT_FULL):
        with pytest.raises(AgentForbidden):
            service.request(engagement.id, MonthlyReportRequest(month="2026-10"), agent)
    service.request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )
    for agent in (AGENT, AGENT_FULL):
        with pytest.raises(AgentForbidden):
            service.approve(engagement.id, "2026-10", MonthlyReportApproval(), agent)
    with pytest.raises(PermissionDenied):
        service.approve(engagement.id, "2026-10", MonthlyReportApproval(), COLLABORATOR)
    with pytest.raises(PermissionDenied):
        service.request(engagement.id, MonthlyReportRequest(month="2026-10"), COLLABORATOR)


def test_an_admin_approves_a_month_and_may_hold_the_days_the_client_contested(
    db_session: Session,
) -> None:
    engagement = _engagement(db_session)
    service = MonthlyReportService(db_session, _settings())
    service.request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )

    with pytest.raises(ValidationFailed, match="serve il motivo"):
        service.approve(
            engagement.id, "2026-10", MonthlyReportApproval(held_dates=[D(2026, 10, 5)]), ADMIN
        )
    with pytest.raises(ValidationFailed, match="indica i giorni"):
        service.approve(engagement.id, "2026-10", MonthlyReportApproval(objection="no"), ADMIN)
    with pytest.raises(ValidationFailed, match="del mese"):
        service.approve(
            engagement.id,
            "2026-10",
            MonthlyReportApproval(held_dates=[D(2026, 11, 2)], objection="no"),
            ADMIN,
        )
    with pytest.raises(NotFound):
        service.approve(engagement.id, "2026-09", MonthlyReportApproval(), ADMIN)

    done = service.approve(
        engagement.id,
        "2026-10",
        MonthlyReportApproval(
            held_dates=[D(2026, 10, 6), D(2026, 10, 5)], objection="Non erano in sede"
        ),
        ADMIN,
    )
    assert (done.state, done.effective_state, done.decided_on) == (
        "approved",
        "approved",
        D(2026, 11, 20),
    )
    assert done.held_dates == [D(2026, 10, 5), D(2026, 10, 6)]
    assert done.objection == "Non erano in sede"
    # Deciding again lifts the hold.
    again = service.approve(engagement.id, "2026-10", MonthlyReportApproval(), ADMIN)
    assert (again.held_dates, again.objection) == ([], None)


def test_a_day_cannot_be_held_once_the_tacit_date_passed(db_session: Session) -> None:
    engagement = _engagement(db_session)
    service = MonthlyReportService(db_session, _settings())
    service.request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 3)), ADMIN
    )
    with pytest.raises(Conflict, match="termine per contestare"):
        service.approve(
            engagement.id,
            "2026-10",
            MonthlyReportApproval(held_dates=[D(2026, 10, 5)], objection="troppo tardi"),
            ADMIN,
        )
    # An approval with nothing held is still possible: it only writes down what already counts.
    assert (
        service.approve(engagement.id, "2026-10", MonthlyReportApproval(), ADMIN).state
        == "approved"
    )
    # Approving first and holding after the deadline is the same thing, and is refused too.
    with pytest.raises(Conflict, match="termine per contestare"):
        service.approve(
            engagement.id,
            "2026-10",
            MonthlyReportApproval(held_dates=[D(2026, 10, 5)], objection="ormai tardi"),
            ADMIN,
        )


def test_deciding_again_keeps_the_first_decision_and_can_lift_a_hold(db_session: Session) -> None:
    engagement = _engagement(db_session)
    service = MonthlyReportService(db_session, _settings())
    service.request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )
    held = MonthlyReportApproval(held_dates=[D(2026, 10, 5)], objection="Assente")
    first = service.approve(engagement.id, "2026-10", held, ADMIN)
    # The deadline has not passed (20 November, tacit date 27 November): the hold may change.
    again = service.approve(engagement.id, "2026-10", MonthlyReportApproval(), ADMIN)
    assert again.held_dates == [] and again.decided_on == first.decided_on


# ---- the report, with a door that answers what the freelancer's space holds ------------------


class FakeDoor:
    """What `EngagementService.report` answers, for one match; every call is recorded."""

    answer: EngagementReport | Exception
    calls: list[tuple[UUID, dt.date, dt.date]] = []

    def __init__(self, *_: Any) -> None: ...

    def report(self, match_id: UUID, date_from: dt.date, date_to: dt.date) -> EngagementReport:
        FakeDoor.calls.append((match_id, date_from, date_to))
        if isinstance(FakeDoor.answer, Exception):
            raise FakeDoor.answer
        return FakeDoor.answer


def _answer(
    *, days: list[ReportWorkUnit] | None = None, hours: list[ReportEntry] | None = None
) -> EngagementReport:
    return EngagementReport(
        slug="ada",
        deal_url="https://pigro.test/ada/deals/x",
        deal=ReportDeal(
            id=uuid4(), name="Lettera", hourly_rate=None, budgeted_hours=None, status="ok"
        ),
        days=hours or [],
        total_hours=Decimal(0),
        billed_hours=Decimal(0),
        unbilled_hours=Decimal(0),
        invoices=[],
        work_units=days or [],
    )


def _unit(day: dt.date, quantity: str = "1", status: str = "worked") -> ReportWorkUnit:
    return ReportWorkUnit(
        date=day, quantity=Decimal(quantity), status=status, description="lavoro", billed=False
    )


def _reader(session: Session, **settings: Any) -> DaysReportService:
    return DaysReportService(
        session,
        _settings(**settings),
        space_slug=None,
        registry_engine=object(),  # type: ignore[arg-type]
    )


@pytest.fixture
def fake_door(monkeypatch: pytest.MonkeyPatch) -> type[FakeDoor]:
    FakeDoor.calls = []
    FakeDoor.answer = _answer()
    monkeypatch.setattr(days_report, "EngagementService", FakeDoor)
    # The registry is not there: the owner check has its own test with the real door.
    monkeypatch.setattr(DaysReportService, "_check_owner", lambda *_: None)
    return FakeDoor


def _month(report: EngagementDaysReport, label: str) -> Any:
    return next(month for month in report.months if month.label == label)


def test_days_read_live_are_not_payable_until_their_month_is_approved(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session)
    fake_door.answer = _answer(days=[_unit(D(2026, 10, 5)), _unit(D(2026, 10, 6), "0.5")])
    reader = _reader(db_session)

    before = reader.report(engagement.id, AGENT)
    october = _month(before, "2026-10")
    assert (october.quantity, october.payable_quantity, october.payable_amount) == (
        Decimal("1.5"),
        Decimal(0),
        Decimal("0.00"),
    )
    assert october.monthly_report is None and not any(day.payable for day in before.days)

    MonthlyReportService(db_session, _settings()).request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )
    assert _month(reader.report(engagement.id, ADMIN), "2026-10").payable_quantity == 0

    MonthlyReportService(db_session, _settings()).approve(
        engagement.id, "2026-10", MonthlyReportApproval(), ADMIN
    )
    after = reader.report(engagement.id, ADMIN)
    october = _month(after, "2026-10")
    assert (october.payable_quantity, october.payable_amount) == (Decimal("1.5"), Decimal("600.00"))
    assert (
        october.monthly_report is not None and october.monthly_report.effective_state == "approved"
    )
    assert all(day.payable for day in after.days)
    assert (after.payable_quantity, after.payable_amount) == (Decimal("1.5"), Decimal("600.00"))


def test_a_month_with_no_objection_past_its_tacit_date_reads_payable(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session)
    fake_door.answer = _answer(days=[_unit(D(2026, 10, 5))])
    MonthlyReportService(db_session, _settings()).request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 3)), ADMIN
    )
    october = _month(_reader(db_session).report(engagement.id, ADMIN), "2026-10")
    assert october.monthly_report.state == "requested"
    assert october.monthly_report.effective_state == "tacit"
    assert october.payable_quantity == Decimal("1")


def test_a_held_day_is_not_payable_while_the_rest_of_the_month_is(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session)
    fake_door.answer = _answer(days=[_unit(D(2026, 10, 5)), _unit(D(2026, 10, 6))])
    service = MonthlyReportService(db_session, _settings())
    service.request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )
    service.approve(
        engagement.id,
        "2026-10",
        MonthlyReportApproval(held_dates=[D(2026, 10, 6)], objection="Assente"),
        ADMIN,
    )
    report = _reader(db_session).report(engagement.id, ADMIN)
    october = _month(report, "2026-10")
    assert (october.payable_quantity, october.held_quantity) == (Decimal("1"), Decimal("1"))
    assert {day.date: (day.held, day.payable) for day in report.days} == {
        D(2026, 10, 5): (False, True),
        D(2026, 10, 6): (True, False),
    }


def test_only_work_the_ledger_holds_as_done_and_inside_the_term_can_be_payable(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session, ends_on=D(2026, 10, 20))
    fake_door.answer = _answer(
        days=[
            _unit(D(2026, 10, 5), status="proposed"),
            _unit(D(2026, 10, 6), status="disputed"),
            _unit(D(2026, 10, 7), status="non_billable"),
            _unit(D(2026, 10, 8), status="paid"),
            _unit(D(2026, 10, 9), status="worked_without_approval"),
            _unit(D(2026, 10, 27), status="worked"),
        ]
    )
    service = MonthlyReportService(db_session, _settings())
    service.request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )
    service.approve(engagement.id, "2026-10", MonthlyReportApproval(), ADMIN)
    report = _reader(db_session).report(engagement.id, ADMIN)
    assert {day.date.day: day.payable for day in report.days} == {
        5: False,
        6: False,
        7: False,
        8: True,
        9: True,
        27: False,
    }
    assert next(day for day in report.days if day.date.day == 27).in_term is False
    assert _month(report, "2026-10").payable_quantity == Decimal("2")


def test_an_hourly_letter_pays_approved_hours_and_old_entries_are_days_at_eight_hours(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    hourly = _engagement(db_session, fee_unit="hour", fee=Decimal("50"))
    daily = _engagement(db_session)
    entry = ReportEntry(
        date=D(2026, 10, 12), hours=Decimal("4.00"), description="ore", billable=True, invoice=None
    )
    unbillable = ReportEntry(
        date=D(2026, 10, 13), hours=Decimal("8.00"), description="no", billable=False, invoice=None
    )
    fake_door.answer = _answer(days=[_unit(D(2026, 10, 5), "6")], hours=[entry, unbillable])
    service = MonthlyReportService(db_session, _settings())
    for engagement in (hourly, daily):
        service.request(
            engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
        )
        service.approve(engagement.id, "2026-10", MonthlyReportApproval(), ADMIN)

    hours = _reader(db_session).report(hourly.id, ADMIN)
    assert (hours.unit, hours.payable_quantity, hours.payable_amount) == (
        "hour",
        Decimal("10.00"),
        Decimal("500.00"),
    )
    days = _reader(db_session).report(daily.id, ADMIN)
    assert (days.unit, days.payable_quantity, days.payable_amount) == (
        "day",
        Decimal("6.50"),
        Decimal("2600.00"),
    )


def test_old_hours_become_days_without_rounding_each_entry(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session)
    entries = [
        ReportEntry(
            date=D(2026, 10, day),
            hours=Decimal("1.00"),
            description="ora",
            billable=True,
            invoice=None,
        )
        for day in (5, 6, 7)
    ]
    fake_door.answer = _answer(hours=entries)
    service = MonthlyReportService(db_session, _settings())
    service.request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )
    service.approve(engagement.id, "2026-10", MonthlyReportApproval(), ADMIN)
    report = _reader(db_session).report(engagement.id, ADMIN)
    # Three hours are 0.375 of a day: 150.00 at 400 a day, not 144.00.
    assert (report.payable_quantity, report.payable_amount) == (Decimal("0.375"), Decimal("150.00"))


def test_a_flat_fee_has_days_but_no_amount_to_read_from_them(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session, fee_unit="flat", fee=Decimal("9000"))
    fake_door.answer = _answer(days=[_unit(D(2026, 10, 5))])
    report = _reader(db_session).report(engagement.id, ADMIN)
    assert report.payable_amount is None and _month(report, "2026-10").payable_amount is None


def test_the_period_is_read_in_windows_and_defaults_to_the_engagement(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session)
    report = _reader(db_session).report(engagement.id, ADMIN)
    assert fake_door.calls == [(engagement.id, D(2026, 10, 1), D(2026, 11, 20))]
    assert (report.date_from, report.date_to) == (D(2026, 10, 1), D(2026, 11, 20))
    assert [month.label for month in report.months] == ["2026-10", "2026-11"]

    fake_door.calls.clear()
    _reader(db_session).report(engagement.id, ADMIN, D(2020, 1, 1), D(2026, 1, 1))
    assert len(fake_door.calls) == 3
    assert fake_door.calls[0][1] == D(2020, 1, 1) and fake_door.calls[-1][2] == D(2026, 1, 1)

    with pytest.raises(ValidationFailed, match="la fine"):
        _reader(db_session).report(engagement.id, ADMIN, D(2026, 11, 2), D(2026, 11, 1))


def test_an_engagement_the_door_does_not_know_yet_answers_empty_with_the_reason(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session, state="draft", hub_match_id=uuid4())
    fake_door.answer = NotFound("engagement", "x")
    report = _reader(db_session).report(engagement.id, ADMIN)
    assert report.available is False and report.days == [] and report.note is not None
    assert fake_door.calls[0][0] == engagement.hub_match_id

    fake_door.answer = SpaceUnreachable(uuid4())
    with pytest.raises(Conflict, match="non e' raggiungibile"):
        _reader(db_session).report(engagement.id, ADMIN)


def test_the_report_is_read_only_from_the_leads_space_and_by_an_admin(
    db_session: Session, fake_door: type[FakeDoor]
) -> None:
    engagement = _engagement(db_session)
    with pytest.raises(Conflict, match="PIGROCRM_LEADS_SPACE"):
        _reader(db_session, leads_space="").report(engagement.id, ADMIN)
    with pytest.raises(Conflict, match="PIGROCRM_LEADS_SPACE"):
        DaysReportService(
            db_session,
            _settings(),
            space_slug="ada",
            registry_engine=object(),  # type: ignore[arg-type]
        ).report(engagement.id, ADMIN)
    with pytest.raises(PermissionDenied):
        _reader(db_session).report(engagement.id, COLLABORATOR)
    with pytest.raises(NotFound):
        _reader(db_session).report(uuid4(), ADMIN)
    # An agent reads it with the administrator's token: it is a read.
    assert _reader(db_session).report(engagement.id, AGENT).available is True


# ---- end to end, through the real door -------------------------------------------------------


def test_the_door_reads_the_work_units_of_the_contract_and_the_report_makes_them_payable(
    db_session: Session, door: Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    match_id = uuid4()
    engagement = _engagement(db_session, hub_match_id=match_id, email="Ada@studio.it")
    answer = _seed(door, match_id)

    with _space(door, "ada-lovelace") as space:
        days = WorkUnitService(space)
        for day, status in (
            (D(2026, 10, 5), "worked"),
            (D(2026, 10, 6), "worked"),
            (D(2026, 10, 7), "proposed"),
        ):
            days.create(
                WorkUnitCreate(
                    contract_id=answer.contract_id,
                    date=day,
                    quantity=Decimal("1"),
                    description="lavoro",
                    status=status,
                ),
                Actor.system(),
            )
        _log(space, answer.deal_id, D(2026, 10, 8), "ore sul vecchio deal")

    settings = door.settings.model_copy(update={"leads_space": "rebase", "root_slug": "rebase"})
    reader = DaysReportService(db_session, settings, registry_engine=door.registry)

    report = reader.report(engagement.id, ADMIN)
    assert report.available is True
    assert [(day.date.day, day.source, day.counts, day.payable) for day in report.days] == [
        (5, "work_unit", True, False),
        (6, "work_unit", True, False),
        (7, "work_unit", False, False),
        (8, "time_entry", True, False),
    ]
    assert _month(report, "2026-10").quantity == Decimal("3.00")

    MonthlyReportService(db_session, _settings()).request(
        engagement.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 18)), ADMIN
    )
    MonthlyReportService(db_session, _settings()).approve(
        engagement.id, "2026-10", MonthlyReportApproval(), ADMIN
    )
    paid = reader.report(engagement.id, ADMIN)
    # Two worked days and eight hours (a day) of the old deal; the proposed day waits.
    assert (paid.payable_quantity, paid.payable_amount) == (Decimal("3.00"), Decimal("1200.00"))
    assert [day.payable for day in paid.days] == [True, True, False, True]


def test_a_match_that_is_not_the_freelancers_reads_nothing(db_session: Session, door: Door) -> None:
    match_id = uuid4()
    _seed(door, match_id)
    stranger = _engagement(db_session, hub_match_id=match_id, email="someone@else.it")
    settings = door.settings.model_copy(update={"leads_space": "rebase", "root_slug": "rebase"})
    with pytest.raises(Conflict, match="non e' di questo freelance"):
        DaysReportService(db_session, settings, registry_engine=door.registry).report(
            stranger.id, ADMIN
        )
