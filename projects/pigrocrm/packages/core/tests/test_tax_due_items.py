"""REB-1074: the income taxes' balances and advances as due items (taxes design § 2 and § 5).

What is proved here: the historical and the forecast advance on the same year, a declared figure
replacing the estimate and recomputing the advances with the difference a paid one leaves carried to
the next item, a company's dates for a fiscal year that is not the calendar year, the thresholds, a
figure that is missing giving no item and saying why, and who may choose the method and read the
items. The estimate is a stub: its own arithmetic has its tests (`test_fiscal_estimate.py`) and
the point here is what the due items do with a figure, so each test says which estimate it stands
on. Dates are pinned with `Clock`, since the pack's dates are fixed days of 2026 and 2027.
"""

import datetime as dt
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, AGENT_FULL, COLLABORATOR, READONLY, CashWorld
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import (
    RESTRICTED_SOURCE_KINDS,
    SOURCE_KINDS,
    DueItem,
)
from pigrocrm.core.due_items.schedule_queries import kinds_of_modules_off
from pigrocrm.core.due_items.schemas import DueItemListQuery, SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import AgentForbidden, PermissionDenied
from pigrocrm.core.fiscal import tax_items
from pigrocrm.core.fiscal.advance_method import TaxAdvanceMethodService
from pigrocrm.core.fiscal.advance_schemas import TaxAdvanceMethodRead, TaxAdvanceMethodSet
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.models import FiscalProfile, TaxAdvanceMethod
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.legal_entities.models import LegalEntity

D = Decimal
TAX_KINDS = ("tax_advance", "tax_balance")


class Clock:
    """The day the writer believes it is."""

    def __init__(self, today: dt.date) -> None:
        self.today = today


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock(dt.date(2026, 5, 15))
    monkeypatch.setattr(tax_items, "today_local", lambda: clock.today)
    return clock


class Estimates:
    """The estimate per year and tax the stub answers; a year not set has none."""

    def __init__(self) -> None:
        self.by_year: dict[int, dict[str, Decimal | None]] = {}

    def set(self, year: int, tax: str, amount: str | None) -> None:
        self.by_year.setdefault(year, {})[tax] = None if amount is None else D(amount)


@pytest.fixture
def estimates(monkeypatch: pytest.MonkeyPatch) -> Estimates:
    stub = Estimates()

    def estimated_taxes(
        self: AnalyticsService, year: int, legal_entity: LegalEntity, profile: object
    ) -> dict[str, Decimal | None]:
        return dict(stub.by_year.get(year, {}))

    monkeypatch.setattr(AnalyticsService, "estimated_taxes", estimated_taxes)
    return stub


def _entity(
    session: Session,
    pack_id: str = "it-flat-rate",
    *,
    isa: bool | None = None,
    year_end: tuple[int, int] = (12, 31),
) -> UUID:
    entity = LegalEntity(
        name=f"tasse {uuid4().hex[:6]}",
        legal_name="Tasse srl",
        country="IT",
        fiscal_year_end_month=year_end[0],
        fiscal_year_end_day=year_end[1],
    )
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id=pack_id,
            regime_code="RF19" if pack_id == "it-flat-rate" else "RF01",
            profitability_coefficient=D("78.00") if pack_id == "it-flat-rate" else None,
            substitute_tax_rate=D("5.00") if pack_id == "it-flat-rate" else None,
            social_security_rate=D("26.07") if pack_id == "it-flat-rate" else None,
            isa=isa,
        )
    )
    session.flush()
    return entity.id


class Row(NamedTuple):
    kind: str
    tax: str | None
    due: dt.date | None
    amount: Decimal
    state: str
    text: str | None


def _live(session: Session, entity: UUID) -> list[Row]:
    session.expire_all()
    rows = session.scalars(
        select(DueItem)
        .where(
            DueItem.legal_entity_id == entity,
            DueItem.source_kind.in_(TAX_KINDS),
            DueItem.state != "dropped",
        )
        .order_by(DueItem.due_date, DueItem.source_kind, DueItem.tax)
    )
    return [Row(r.source_kind, r.tax, r.due_date, r.amount, r.state, r.description) for r in rows]


def _dropped(session: Session, entity: UUID) -> list[DueItem]:
    return list(
        session.scalars(
            select(DueItem).where(
                DueItem.legal_entity_id == entity,
                DueItem.source_kind.in_(TAX_KINDS),
                DueItem.state == "dropped",
            )
        )
    )


def _declare(session: Session, entity: UUID, tax: str, year: int, **figures: str | None) -> None:
    DeclaredTaxFigureService(session).set_declared_tax_figure(
        DeclaredTaxFigureSet(
            tax=tax,  # type: ignore[arg-type]
            year=year,
            **{key: None if value is None else D(value) for key, value in figures.items()},  # type: ignore[arg-type]
        ),
        ADMIN,
        legal_entity_id=entity,
    )


def _method(
    session: Session, entity: UUID, year: int, method: str, actor: Actor = ADMIN
) -> TaxAdvanceMethodRead:
    return TaxAdvanceMethodService(session).set_tax_advance_method(
        TaxAdvanceMethodSet(year=year, method=method),  # type: ignore[arg-type]
        actor,
        legal_entity_id=entity,
    )


def _pay(session: Session, entity: UUID, due: dt.date, amount: str) -> None:
    item = session.scalars(
        select(DueItem).where(
            DueItem.legal_entity_id == entity,
            DueItem.source_kind.in_(TAX_KINDS),
            DueItem.state == "expected",
            DueItem.due_date == due,
        )
    ).one()
    DueItemService(session).settle(
        item.id, SettlementCreate(amount=D(amount), settled_on=dt.date(2026, 5, 1)), ADMIN
    )


# --- the historical and the forecast advance on the same year ---------------------------------


def test_the_historical_advance_reads_last_years_estimate_and_the_forecast_one_this_years(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    estimates.set(2026, "substitute_tax", "3000.00")

    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)

    # By default the historical method: 100% of the 2025 estimate, 50% on each date, forecast
    # because nothing declared it.
    assert _live(db_session, entity) == [
        Row(
            "tax_advance",
            "substitute_tax",
            dt.date(2026, 6, 30),
            D("500.00"),
            "forecast",
            "Acconto imposta sostitutiva 2026, 1a rata",
        ),
        Row(
            "tax_advance",
            "substitute_tax",
            dt.date(2026, 11, 30),
            D("500.00"),
            "forecast",
            "Acconto imposta sostitutiva 2026, 2a rata",
        ),
    ]
    assert report.written == 2
    # The balance of 2025 needs the advances of 2025, which the pack has no rule for: no item,
    # and the report says why.
    assert [(n.tax, n.year, n.part) for n in report.not_written] == [
        ("substitute_tax", 2025, "balance")
    ]
    assert "no advance rule" in report.not_written[0].reason

    chosen = _method(db_session, entity, 2026, "forecast")
    assert (chosen.year, chosen.method) == (2026, "forecast")
    assert (chosen.items.written, chosen.items.dropped) == (2, 2)
    assert [(r.due, r.amount, r.state) for r in _live(db_session, entity)] == [
        (dt.date(2026, 6, 30), D("1500.00"), "forecast"),
        (dt.date(2026, 11, 30), D("1500.00"), "forecast"),
    ]
    # The two historical items are dropped, not edited.
    assert sorted(i.amount for i in _dropped(db_session, entity)) == [D("500.00"), D("500.00")]

    back = _method(db_session, entity, 2026, "historical")
    assert (back.items.written, back.items.dropped) == (2, 2)
    assert [r.amount for r in _live(db_session, entity)] == [D("500.00"), D("500.00")]


def test_the_method_is_per_year_and_a_year_with_no_choice_is_historical(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    estimates.set(2026, "substitute_tax", "3000.00")
    _method(db_session, entity, 2027, "forecast")
    # 2026 is untouched by a choice for 2027, and choosing again replaces the row.
    assert [r.amount for r in _live(db_session, entity)] == [D("500.00"), D("500.00")]
    _method(db_session, entity, 2027, "historical")
    rows = db_session.scalars(select(TaxAdvanceMethod)).all()
    assert [(r.tax_year, r.method) for r in rows] == [(2027, "historical")]


def test_the_forecast_method_without_an_estimate_writes_nothing_and_says_why(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert len(_live(db_session, entity)) == 2
    chosen = _method(db_session, entity, 2026, "forecast")
    assert _live(db_session, entity) == []
    assert chosen.items.dropped == 2
    reasons = {(n.year, n.part): n.reason for n in chosen.items.not_written}
    assert (
        "forecast method needs the imposta sostitutiva estimate of 2026"
        in reasons[(2026, "first_advance")]
    )


# --- a declared figure replaces the estimate --------------------------------------------------


def test_a_declared_figure_replaces_the_estimate_and_the_difference_goes_to_the_next_item(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert {r.state for r in _live(db_session, entity)} == {"forecast"}

    # The accountant's return says the same tax: the items become `expected`, and the forecast
    # they replace is dropped.
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="1000.00")
    assert [(r.due, r.amount, r.state) for r in _live(db_session, entity)] == [
        (dt.date(2026, 6, 30), D("500.00"), "expected"),
        (dt.date(2026, 11, 30), D("500.00"), "expected"),
    ]
    assert {i.state for i in _dropped(db_session, entity)} == {"dropped"}
    assert len(_dropped(db_session, entity)) == 2

    # The first advance is paid at 500. The return is corrected to 1200: the advance should have
    # been 600, the 100 it was short is owed with the second one, which a person is asked to
    # confirm.
    _pay(db_session, entity, dt.date(2026, 6, 30), "500.00")
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="1200.00")
    first, second = _live(db_session, entity)
    assert (first.amount, first.state) == (D("500.00"), "settled")
    assert (second.amount, second.state) == (D("700.00"), "expected")
    assert second.text is not None
    assert "+100,00" in second.text and "da confermare" in second.text

    # Corrected again, to 900: the first should have been 450, so the second is 450 less the 50 paid
    # over.
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="900.00")
    assert _live(db_session, entity)[1].amount == D("400.00")
    assert "-50,00" in (_live(db_session, entity)[1].text or "")

    # Saying nothing new changes nothing: no item is dropped and recreated for the same figures.
    again = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert (again.written, again.dropped) == (0, 0)


def test_what_a_paid_second_advance_leaves_goes_to_the_balance_of_the_year(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="900.00")
    _pay(db_session, entity, dt.date(2026, 6, 30), "450.00")
    _pay(db_session, entity, dt.date(2026, 11, 30), "450.00")
    # The year's own return, typed later: 2000 owed. The advances were 450 and 450.
    clock.today = dt.date(2026, 7, 15)
    _declare(db_session, entity, "substitute_tax", 2026, tax_due="2000.00")
    # Nothing was paid short yet: the balance is the tax less what was paid.
    balance = [r for r in _live(db_session, entity) if r.kind == "tax_balance"]
    assert [(r.due, r.amount, r.state) for r in balance] == [
        (dt.date(2027, 6, 30), D("1100.00"), "expected")
    ]
    assert "da confermare" not in (balance[0].text or "")

    # The 2025 return is corrected to 1200 after both advances were paid: they should have been
    # 600 and 600, so 300 is left, and it is in the balance, which says so.
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="1200.00")
    balance = [r for r in _live(db_session, entity) if r.kind == "tax_balance"]
    assert [(r.amount, r.state) for r in balance] == [(D("1100.00"), "expected")]
    assert "+300,00" in (balance[0].text or "") and "da confermare" in (balance[0].text or "")


def test_a_declared_balance_is_the_balance_the_accountant_computed(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    clock.today = dt.date(2026, 7, 15)
    estimates.set(2026, "substitute_tax", "2000.00")
    estimates.set(2025, "substitute_tax", "1000.00")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    # Estimate 2000 less the 2026 advances of 500 and 500 (the first assumed paid as planned).
    assert [
        (r.due, r.amount, r.state) for r in _live(db_session, entity) if r.kind == "tax_balance"
    ] == [(dt.date(2027, 6, 30), D("1000.00"), "forecast")]
    _declare(db_session, entity, "substitute_tax", 2026, balance="640.00")
    assert [(r.amount, r.state) for r in _live(db_session, entity) if r.kind == "tax_balance"] == [
        (D("640.00"), "expected")
    ]
    # A credit is no due item.
    _declare(db_session, entity, "substitute_tax", 2026, balance="-80.00")
    assert [r for r in _live(db_session, entity) if r.kind == "tax_balance"] == []


def test_a_payment_touches_an_item_and_a_refresh_never_changes_or_drops_it(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="1000.00")
    _pay(db_session, entity, dt.date(2026, 6, 30), "200.00")  # partly paid
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="2000.00")
    first = _live(db_session, entity)[0]
    # Partly paid: it keeps its amount and its remainder, with no re-split.
    assert (first.amount, first.state) == (D("500.00"), "expected")
    # Nothing owed any more: the unpaid item goes, the partly paid one stays.
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="10.00")
    assert [(r.due, r.amount) for r in _live(db_session, entity)] == [
        (dt.date(2026, 6, 30), D("500.00"))
    ]


# --- thresholds, split and single payment, end to end ---------------------------------------


def test_a_person_pays_a_small_advance_once_and_no_advance_at_the_floor(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-standard", isa=False)
    _declare(db_session, entity, "irpef", 2025, tax_due="200.00")
    assert [(r.tax, r.due, r.amount, r.text) for r in _live(db_session, entity)] == [
        ("irpef", dt.date(2026, 11, 30), D("200.00"), "Acconto IRPEF 2026, in unica soluzione")
    ]
    _declare(db_session, entity, "irpef", 2025, tax_due="51.65")
    assert _live(db_session, entity) == []
    _declare(db_session, entity, "irpef", 2025, tax_due="1000.00")
    assert [(r.due, r.amount) for r in _live(db_session, entity)] == [
        (dt.date(2026, 6, 30), D("400.00")),
        (dt.date(2026, 11, 30), D("600.00")),
    ]


def test_an_isa_person_splits_in_two_equal_halves(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-standard", isa=True)
    _declare(db_session, entity, "irpef", 2025, tax_due="1000.00")
    assert [r.amount for r in _live(db_session, entity)] == [D("500.00"), D("500.00")]


def test_a_flat_rate_advance_up_to_206_is_paid_once(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="206.00")
    assert [(r.due, r.amount) for r in _live(db_session, entity)] == [
        (dt.date(2026, 11, 30), D("206.00"))
    ]
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="206.01")
    assert [(r.due, r.amount) for r in _live(db_session, entity)] == [
        (dt.date(2026, 6, 30), D("103.01")),
        (dt.date(2026, 11, 30), D("103.00")),
    ]


def test_an_srl_has_ires_and_irap_advances_with_its_own_thresholds(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-company", isa=False)
    _declare(db_session, entity, "ires", 2025, tax_due="10000.00")
    _declare(db_session, entity, "irap", 2025, tax_due="20.66")
    _declare(db_session, entity, "irap", 2025, tax_due="100.00")
    rows = _live(db_session, entity)
    assert [(r.tax, r.due, r.amount) for r in rows] == [
        ("irap", dt.date(2026, 6, 30), D("40.00")),
        ("ires", dt.date(2026, 6, 30), D("4000.00")),
        ("irap", dt.date(2026, 11, 30), D("60.00")),
        ("ires", dt.date(2026, 11, 30), D("6000.00")),
    ]
    # Up to 103 an IRES advance is one payment; 20.66 or less is none at all.
    _declare(db_session, entity, "ires", 2025, tax_due="103.00")
    _declare(db_session, entity, "irap", 2025, tax_due="20.66")
    assert [(r.tax, r.due, r.amount) for r in _live(db_session, entity)] == [
        ("ires", dt.date(2026, 11, 30), D("103.00"))
    ]


def test_a_profile_that_does_not_say_isa_gets_no_person_or_company_advance(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-standard", isa=None)
    _declare(db_session, entity, "irpef", 2025, tax_due="1000.00")
    assert _live(db_session, entity) == []
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert {(n.tax, n.part) for n in report.not_written} >= {
        ("irpef", "first_advance"),
        ("irpef", "second_advance"),
    }
    assert all("ISA" in n.reason for n in report.not_written if n.part != "balance")


def test_a_tax_with_no_estimate_and_no_declared_figure_has_no_item_and_says_so(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-standard", isa=False)
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert _live(db_session, entity) == []
    reasons = {(n.year, n.part): n.reason for n in report.not_written}
    assert "no declared IRPEF and no estimate for 2025" in reasons[(2026, "first_advance")]


# --- a company's dates follow its fiscal year end ----------------------------------------------


def test_a_company_with_a_year_ending_on_30_june_pays_on_its_own_dates(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    calendar = _entity(db_session, "it-company", isa=False)
    june = _entity(db_session, "it-company", isa=False, year_end=(6, 30))
    for entity in (calendar, june):
        _declare(db_session, entity, "ires", 2025, tax_due="10000.00")
    assert [(r.due, r.amount) for r in _live(db_session, calendar)] == [
        (dt.date(2026, 6, 30), D("4000.00")),
        (dt.date(2026, 11, 30), D("6000.00")),
    ]
    # Tax year 2026 of a year that starts on 1 July 2026: the first advance is the balance's day of
    # the year before and the second falls on the last day of the eleventh month of the period.
    assert [(r.due, r.amount) for r in _live(db_session, june)] == [
        (dt.date(2026, 12, 31), D("4000.00")),
        (dt.date(2027, 5, 31), D("6000.00")),
    ]


def test_a_year_end_in_the_middle_of_a_month_gets_no_company_item_and_says_why(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-company", isa=False, year_end=(2, 15))
    _declare(db_session, entity, "ires", 2025, tax_due="10000.00")
    assert _live(db_session, entity) == []
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert any("last day of a month" in n.reason for n in report.not_written)


# --- who may choose the method and who reads the items ----------------------------------------


@pytest.mark.parametrize(
    ("actor", "refusal"),
    [(COLLABORATOR, PermissionDenied), (READONLY, PermissionDenied), (AGENT, AgentForbidden)],
)
def test_the_method_is_an_administrators_and_a_default_token_is_refused(
    db_session: Session,
    clock: Clock,
    estimates: Estimates,
    actor: Actor,
    refusal: type[Exception],
) -> None:
    entity = _entity(db_session)
    with pytest.raises(refusal):
        _method(db_session, entity, 2026, "forecast", actor)
    assert db_session.scalars(select(TaxAdvanceMethod)).all() == []
    # A token that carries `full_access` over an administrator may.
    assert _method(db_session, entity, 2026, "forecast", AGENT_FULL).method == "forecast"


def test_a_tax_due_item_is_read_by_administrators_and_full_access_tokens_alone(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="1000.00")
    item = db_session.scalars(
        select(DueItem).where(DueItem.source_kind == "tax_advance", DueItem.state == "expected")
    ).first()
    assert item is not None
    service = DueItemService(db_session)
    query = DueItemListQuery(legal_entity_id=entity)
    for actor in (ADMIN, AGENT_FULL):
        assert {i.source_kind for i in service.list(query, actor).items} == {"tax_advance"}
        assert service.get(item.id, actor).tax == "substitute_tax"
    for actor in (COLLABORATOR, READONLY, AGENT):
        assert service.list(query, actor).items == [], actor
        with pytest.raises((PermissionDenied, AgentForbidden)):
            service.get(item.id, actor)
        with pytest.raises((PermissionDenied, AgentForbidden)):
            service.list(DueItemListQuery(source_kind="tax_balance"), actor)


def test_the_schedule_leaves_the_tax_kinds_out_for_anybody_who_may_not_read_them(
    db_session: Session,
) -> None:
    from pigrocrm.core.due_items.schedule import CashScheduleService
    from pigrocrm.core.due_items.schedule_schemas import CashScheduleQuery

    world = CashWorld(db_session)
    world.item("500.00", world.week(1), direction="out", source_kind="tax_advance", tax="irpef")
    world.item("900.00", world.week(2), direction="out", source_kind="tax_balance", tax="irpef")

    def out(actor: Actor) -> list[Decimal]:
        page = CashScheduleService(db_session).get_schedule(
            CashScheduleQuery(weeks=13), actor, modules=None, profile="company"
        )
        return [week.out_expected for week in page.weeks[1:3]]  # type: ignore[misc]

    assert out(ADMIN) == [D("500.00"), D("900.00")]
    assert out(AGENT_FULL) == [D("500.00"), D("900.00")]
    for actor in (COLLABORATOR, READONLY, AGENT):
        assert out(actor) == [D("0.00"), D("0.00")], actor


def test_the_items_the_writer_makes_are_the_solo_schedules_tax_rows_for_an_administrator_alone(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    from pigrocrm.core.due_items.schedule import CashScheduleService
    from pigrocrm.core.due_items.schedule_schemas import CashScheduleQuery

    world = CashWorld(db_session)
    db_session.add(
        FiscalProfile(legal_entity_id=world.entity, pack_id="it-flat-rate", regime_code="RF19")
    )
    db_session.flush()
    _declare(db_session, world.entity, "substitute_tax", 2025, tax_due="1000.00")

    def taxes(actor: Actor) -> list[tuple[str | None, str | None, Decimal]] | None:
        page = CashScheduleService(db_session).get_schedule(
            CashScheduleQuery(weeks=13, legal_entity_id=world.entity),
            actor,
            modules=None,
            profile="solo",
        )
        if page.taxes is None:
            return None
        return [(t.tax, t.description, t.amount) for t in page.taxes]

    expected = [
        ("substitute_tax", "Acconto imposta sostitutiva 2026, 1a rata", D("500.00")),
        ("substitute_tax", "Acconto imposta sostitutiva 2026, 2a rata", D("500.00")),
    ]
    assert taxes(ADMIN) == expected
    assert taxes(AGENT_FULL) == expected
    for actor in (COLLABORATOR, READONLY, AGENT):
        assert taxes(actor) is None, actor


def test_the_new_kinds_are_restricted_and_belong_to_the_taxes_module() -> None:
    for kind in TAX_KINDS:
        assert kind in SOURCE_KINDS
        assert kind in RESTRICTED_SOURCE_KINDS
        assert kind in kinds_of_modules_off({"cash"})
        assert kind not in kinds_of_modules_off({"taxes"})


# --- the daily pass recomputes, and one failing legal entity does not stop the rest -----------


def test_the_daily_pass_follows_an_estimate_and_a_second_pass_changes_nothing(
    db_session: Session, estimates: Estimates
) -> None:
    from pigrocrm.core.automations.daily import AutomationPass

    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    day = dt.date(2026, 5, 15)

    report = AutomationPass(db_session).run(today=day)
    assert (report.tax_items_failed, report.tax_items_failures) == (0, ())
    assert report.tax_items_refreshed >= 1
    assert [(r.due, r.amount, r.state) for r in _live(db_session, entity)] == [
        (dt.date(2026, 6, 30), D("500.00"), "forecast"),
        (dt.date(2026, 11, 30), D("500.00"), "forecast"),
    ]
    first_ids = {
        i.id
        for i in db_session.scalars(select(DueItem).where(DueItem.source_kind == "tax_advance"))
    }

    # The estimate moved because invoices were collected: the pass moves the forecasts with it.
    estimates.set(2025, "substitute_tax", "2000.00")
    AutomationPass(db_session).run(today=day)
    assert [r.amount for r in _live(db_session, entity)] == [D("1000.00"), D("1000.00")]

    # And with nothing moved, nothing is dropped or created again.
    ids = {
        i.id
        for i in db_session.scalars(select(DueItem).where(DueItem.source_kind == "tax_advance"))
    }
    AutomationPass(db_session).run(today=day)
    assert {
        i.id
        for i in db_session.scalars(select(DueItem).where(DueItem.source_kind == "tax_advance"))
    } == ids
    assert first_ids < ids


def test_a_legal_entity_whose_refresh_fails_is_counted_and_the_others_go_on(
    db_session: Session, estimates: Estimates, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pigrocrm.core.automations.daily import AutomationPass

    broken = _entity(db_session)
    fine = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    real = TaxDueItemService.refresh_tax_due_items

    def flaky(self: TaxDueItemService, legal_entity_id: UUID, *, today: dt.date | None = None):  # noqa: ANN202
        if legal_entity_id == broken:
            raise RuntimeError("boom")
        return real(self, legal_entity_id, today=today)

    monkeypatch.setattr(TaxDueItemService, "refresh_tax_due_items", flaky)
    report = AutomationPass(db_session).run(today=dt.date(2026, 5, 15))

    assert report.tax_items_failed == 1 and report.tax_items_failures == ("RuntimeError",)
    assert len(_live(db_session, fine)) == 2
    assert _live(db_session, broken) == []


def test_the_real_estimate_feeds_the_items_and_a_year_with_no_revenue_owes_no_advance(
    db_session: Session, clock: Clock
) -> None:
    """No stub: the flat-rate regime's estimate is computed from the legal entity's invoices, and a
    legal entity with none has an estimate of zero, so the advance it makes is none. A person has
    no estimate at all, which `estimated_taxes` says by leaving the tax out."""
    from pigrocrm.core.fiscal.schemas import FiscalProfileRead

    flat = _entity(db_session)
    person = _entity(db_session, "it-standard", isa=False)
    analytics = AnalyticsService(db_session)
    for entity_id, expected in (
        (flat, {"substitute_tax": D("0.00"), "inps": D("0.00")}),
        (person, {}),
    ):
        entity = db_session.get(LegalEntity, entity_id)
        profile = db_session.scalars(
            select(FiscalProfile).where(FiscalProfile.legal_entity_id == entity_id)
        ).one()
        assert entity is not None
        estimated = analytics.estimated_taxes(
            2026, entity, FiscalProfileRead.model_validate(profile)
        )
        assert {tax: figure for tax, figure in estimated.items() if figure is not None} == expected
    report = TaxDueItemService(db_session).refresh_tax_due_items(flat, today=clock.today)
    assert (report.written, _live(db_session, flat)) == (0, [])
