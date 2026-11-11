"""REB-1075: the instalment plan of the June tax payments and the plan of a notice from the tax
office (taxes design § 2 and § 5).

What is proved here: a plan splits the June balance and first advance in equal instalments with the
interest of the Agenzia's own «Rateazione» table and leaves the November advance whole; a plan
that is changed or removed before any payment rewrites the items; once a payment, even a partial
one, is on an instalment nothing is rewritten and the plan is not changed or removed; the refresh
respects a plan; a notice plan writes its instalments as `notice_plan` items and is changed and
deleted the same way; and who may do any of it. The estimate is a stub as in `test_tax_due_items`:
the point is what the writer does with a figure. Dates are pinned with `Clock`.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, AGENT_FULL, COLLABORATOR, READONLY
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schedule import CashScheduleService
from pigrocrm.core.due_items.schedule_schemas import CashScheduleQuery
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.fiscal import tax_items
from pigrocrm.core.fiscal.advances import Missing
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.instalment_plan import TaxInstalmentPlanService
from pigrocrm.core.fiscal.models import (
    FiscalProfile,
    TaxInstalmentPlan,
    TaxNoticePlan,
)
from pigrocrm.core.fiscal.notice_plan import TaxNoticePlanService
from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK, IT_STANDARD_PACK
from pigrocrm.core.fiscal.plan_math import plan_schedule, split_into_instalments
from pigrocrm.core.fiscal.plan_schemas import (
    NoticeInstalmentSet,
    TaxInstalmentPlanResult,
    TaxInstalmentPlanSet,
    TaxNoticePlanSet,
    TaxNoticePlanUpdate,
)
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.legal_entities.models import LegalEntity

D = Decimal
PLAN_KINDS = ("tax_instalment", "tax_balance", "tax_advance", "notice_plan")


class Clock:
    def __init__(self, today: dt.date) -> None:
        self.today = today


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock(dt.date(2026, 5, 15))
    monkeypatch.setattr(tax_items, "today_local", lambda: clock.today)
    return clock


class Estimates:
    """The estimate per year the stub answers; by default no tax has one, so the figures are the
    accountant's, typed."""

    def __init__(self) -> None:
        self.by_year: dict[int, dict[str, Decimal | None]] = {}


@pytest.fixture(autouse=True)
def estimates(monkeypatch: pytest.MonkeyPatch) -> Estimates:
    stub = Estimates()
    monkeypatch.setattr(
        AnalyticsService,
        "estimated_taxes",
        lambda self, year, legal_entity, profile: dict(stub.by_year.get(year, {})),
    )
    return stub


def _entity(session: Session) -> UUID:
    entity = LegalEntity(name=f"rate {uuid4().hex[:6]}", legal_name="Rate srl", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-flat-rate",
            regime_code="RF19",
            profitability_coefficient=D("78.00"),
            substitute_tax_rate=D("5.00"),
            social_security_rate=D("26.07"),
        )
    )
    session.flush()
    return entity.id


def _declare(session: Session, entity: UUID, **figures: str) -> None:
    """The 2025 return of the substitute tax: tax due 1000 (so each advance of 2026 is 500) and
    the balance the accountant computed."""
    DeclaredTaxFigureService(session).set_declared_tax_figure(
        DeclaredTaxFigureSet(
            tax="substitute_tax",
            year=2025,
            **{k: D(v) for k, v in figures.items()},  # type: ignore[arg-type]
        ),
        ADMIN,
        legal_entity_id=entity,
    )


def _plan(
    session: Session,
    entity: UUID,
    instalments: int,
    *,
    actor: Actor = ADMIN,
    tax: str | None = "substitute_tax",
    year: int = 2025,
) -> TaxInstalmentPlanResult:
    return TaxInstalmentPlanService(session).set_tax_instalment_plan(
        TaxInstalmentPlanSet(year=year, tax=tax, instalments=instalments),  # type: ignore[arg-type]
        actor,
        legal_entity_id=entity,
    )


def _items(session: Session, entity: UUID, *, dropped: bool = False) -> list[DueItem]:
    session.expire_all()
    return list(
        session.scalars(
            select(DueItem)
            .where(
                DueItem.legal_entity_id == entity,
                DueItem.source_kind.in_(PLAN_KINDS),
                (DueItem.state == "dropped") if dropped else (DueItem.state != "dropped"),
            )
            .order_by(DueItem.due_date, DueItem.description)
        )
    )


def _rows(items: list[DueItem]) -> list[tuple[str, dt.date | None, Decimal]]:
    return [(i.source_kind, i.due_date, i.amount) for i in items]


def _balance_items(items: list[DueItem]) -> list[DueItem]:
    return [i for i in items if (i.description or "").startswith("Saldo")]


def _advance_items(items: list[DueItem]) -> list[DueItem]:
    return [i for i in items if (i.description or "").startswith("Acconto")]


# --- the schedule and the split, as pure arithmetic ----------------------------------------


def test_the_split_is_equal_with_the_last_instalment_taking_the_cent() -> None:
    schedule = plan_schedule(
        IT_STANDARD_PACK, "irpef", 2025, (12, 31), "balance", dt.date(2026, 6, 30)
    )
    assert not isinstance(schedule, Missing)
    planned = split_into_instalments(D("100.00"), 3, schedule)
    assert [p.principal for p in planned] == [D("33.33"), D("33.33"), D("33.34")]
    assert sum((p.principal for p in planned), D(0)) == D("100.00")
    assert [p.interest_percent for p in planned] == [D("0.00"), D("0.18"), D("0.51")]
    # 33.33 x 0.18% = 0.059994 and 33.34 x 0.51% = 0.170034, rounded to the cent.
    assert [p.interest for p in planned] == [D("0.00"), D("0.06"), D("0.17")]


def test_a_company_with_a_broken_fiscal_year_follows_the_year_its_return_is_filed() -> None:
    """The period 1 July 2025 - 30 June 2026 files in April 2027 (the Agenzia's own example), so
    the balance due 31 December 2026 may be spread over the whole of 2027 to 16 December, not
    just the 16 days of 2026 a calendar rule on the first payment would give."""
    schedule = plan_schedule(
        IT_COMPANY_PACK, "ires", 2025, (6, 30), "balance", dt.date(2026, 12, 31)
    )
    assert not isinstance(schedule, Missing)
    assert schedule[0].due_date == dt.date(2026, 12, 31)
    assert schedule[-1].nominal_date == dt.date(2027, 12, 16)
    assert len(schedule) == 13


def test_the_second_advance_and_a_tax_with_no_rule_cannot_be_split() -> None:
    second = plan_schedule(
        IT_STANDARD_PACK, "irpef", 2025, (12, 31), "second_advance", dt.date(2026, 11, 30)
    )
    assert isinstance(second, Missing) and "second advance" in second.reason
    no_form = plan_schedule(
        IT_STANDARD_PACK,
        "inps",
        2025,
        (12, 31),
        "balance",
        dt.date(2026, 6, 30),  # type: ignore[arg-type]
    )
    assert isinstance(no_form, Missing)


# --- a plan splits the June items --------------------------------------------------------


def test_a_plan_splits_the_balance_with_the_interest_of_the_agenzias_table(
    db_session: Session, clock: Clock
) -> None:
    """Redditi PF 2026 fascicolo 1, «Rateazione»: a first payment on 30 June 2026 has seven
    instalments, the second on 16 July with 0.18% and then 0.33% more each, the last on 16
    December with 1.83%. A balance of 700.00 makes seven of 100.00."""
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="700.00")
    result = _plan(db_session, entity, 7)

    assert [(p.tax, p.tax_year, p.instalments) for p in result.plans] == [
        ("substitute_tax", 2025, 7)
    ]
    balance = _balance_items(_items(db_session, entity))
    assert [(i.due_date, i.amount) for i in balance] == [
        (dt.date(2026, 6, 30), D("100.00")),
        (dt.date(2026, 7, 16), D("100.18")),
        (dt.date(2026, 8, 20), D("100.51")),
        (dt.date(2026, 9, 16), D("100.84")),
        (dt.date(2026, 10, 16), D("101.17")),
        (dt.date(2026, 11, 16), D("101.50")),
        (dt.date(2026, 12, 16), D("101.83")),
    ]
    assert {(i.source_kind, i.state, i.direction, i.counterparty_type, i.tax) for i in balance} == {
        ("tax_instalment", "expected", "out", "tax_office", "substitute_tax")
    }
    assert balance[0].description == "Saldo imposta sostitutiva 2025, rateizzata: rata 1 di 7"
    assert balance[1].description == (
        "Saldo imposta sostitutiva 2025, rateizzata: rata 2 di 7, con interessi dello 0,18%"
    )
    # The single June items are gone, dropped with their reason, never edited.
    dropped = _items(db_session, entity, dropped=True)
    assert sorted((i.source_kind, i.dropped_reason) for i in dropped) == [
        ("tax_advance", "rateizzato"),
        ("tax_balance", "rateizzato"),
    ]


def test_the_first_advance_is_split_with_the_balance_and_the_november_advance_never(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    _plan(db_session, entity, 3)

    items = _items(db_session, entity)
    # 600.00 / 3 = 200.00 each; 500.00 / 3 = 166.67, 166.67 and 166.66. The interest is the
    # schedule's percentage of each instalment: 0, 0.18% and 0.51%.
    assert [(i.due_date, i.amount) for i in _balance_items(items)] == [
        (dt.date(2026, 6, 30), D("200.00")),
        (dt.date(2026, 7, 16), D("200.36")),
        (dt.date(2026, 8, 20), D("201.02")),
    ]
    first_advance = [i for i in _advance_items(items) if i.source_kind == "tax_instalment"]
    assert [(i.due_date, i.amount) for i in first_advance] == [
        (dt.date(2026, 6, 30), D("166.67")),
        (dt.date(2026, 7, 16), D("166.97")),
        (dt.date(2026, 8, 20), D("167.51")),
    ]
    assert first_advance[0].description == (
        "Acconto imposta sostitutiva 2026, 1a rata, rateizzata: rata 1 di 3"
    )
    # The November advance stays one payment, of an item of its own kind.
    november = [i for i in items if i.source_kind == "tax_advance"]
    assert [(i.due_date, i.amount) for i in november] == [(dt.date(2026, 11, 30), D("500.00"))]


def test_the_instalments_are_in_the_cash_schedule_for_an_administrator_alone(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    _plan(db_session, entity, 3)

    def taxes(actor: Actor) -> list[tuple[str | None, str | None, Decimal]] | None:
        page = CashScheduleService(db_session).get_schedule(
            CashScheduleQuery(weeks=26, legal_entity_id=entity),
            actor,
            modules=None,
            profile="solo",
        )
        if page.taxes is None:
            return None
        return [(t.tax, t.description, t.amount) for t in page.taxes]

    seen = taxes(ADMIN)
    assert seen is not None
    assert (
        "substitute_tax",
        "Saldo imposta sostitutiva 2025, rateizzata: rata 1 di 3",
        D("200.00"),
    ) in seen
    assert len([row for row in seen if "rateizzata" in (row[1] or "")]) == 6
    assert taxes(AGENT_FULL) == seen
    for actor in (COLLABORATOR, READONLY, AGENT):
        assert taxes(actor) is None, actor


def test_the_refresh_respects_a_plan_and_follows_the_figures_until_a_payment(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    _plan(db_session, entity, 3)
    before = _rows(_items(db_session, entity))

    # The daily pass: nothing moved, nothing is rewritten and the single items do not come back.
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert (report.written, report.dropped) == (0, 0)
    assert _rows(_items(db_session, entity)) == before

    # A figure that moves is followed, as the single item was: the balance becomes 900.00.
    _declare(db_session, entity, tax_due="1000.00", balance="900.00")
    balance = _balance_items(_items(db_session, entity))
    assert [i.amount for i in balance] == [D("300.00"), D("300.54"), D("301.53")]
    assert not [i for i in _items(db_session, entity) if i.source_kind == "tax_balance"]


def test_changing_or_removing_a_plan_before_any_payment_rewrites_the_items(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    _plan(db_session, entity, 3)
    assert len(_balance_items(_items(db_session, entity))) == 3

    changed = _plan(db_session, entity, 2)
    assert [(p.instalments) for p in changed.plans] == [2]
    assert [i.amount for i in _balance_items(_items(db_session, entity))] == [
        D("300.00"),
        D("300.54"),
    ]
    assert changed.items.written == 4  # two instalments of the balance and of the first advance

    removed = _plan(db_session, entity, 1)
    assert removed.plans == []
    assert db_session.scalars(select(TaxInstalmentPlan)).all() == []
    items = _items(db_session, entity)
    # Back to one payment each, as before any plan: the June balance and first advance, and the
    # November advance.
    assert sorted(_rows(items)) == [
        ("tax_advance", dt.date(2026, 6, 30), D("500.00")),
        ("tax_advance", dt.date(2026, 11, 30), D("500.00")),
        ("tax_balance", dt.date(2026, 6, 30), D("600.00")),
    ]
    assert removed.items.written == 2


def test_all_the_income_taxes_of_a_year_can_be_split_together(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    together = _plan(db_session, entity, 2, tax=None)
    # The flat-rate pack's law carries the substitute tax alone.
    assert [(p.tax, p.instalments) for p in together.plans] == [("substitute_tax", 2)]


def test_more_instalments_than_the_law_allows_are_refused(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    with pytest.raises(ValidationFailed) as refused:
        _plan(db_session, entity, 8)
    assert "al massimo 7" in str(refused.value)
    assert db_session.scalars(select(TaxInstalmentPlan)).all() == []
    # A tax this entity does not owe is refused as well.
    with pytest.raises(ValidationFailed):
        _plan(db_session, entity, 3, tax="ires")


def test_a_plan_over_an_estimate_writes_forecast_instalments_that_can_be_paid(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    """The default state of a flat-rate company: nothing is declared, so the first advance is
    computed on last year's estimate and its instalments are forecasts. No document replaces a tax
    forecast, so paying one makes it the item that was paid."""
    entity = _entity(db_session)
    estimates.by_year[2025] = {"substitute_tax": D("1000.00")}
    _plan(db_session, entity, 3)
    instalments = [i for i in _items(db_session, entity) if i.source_kind == "tax_instalment"]
    assert [(i.state, i.amount) for i in instalments] == [
        ("forecast", D("166.67")),
        ("forecast", D("166.97")),
        ("forecast", D("167.51")),
    ]
    assert all(i.forecast_key is not None for i in instalments)

    _pay(db_session, instalments[0], "166.67")
    db_session.refresh(instalments[0])
    assert (instalments[0].state, instalments[0].forecast_key) == ("settled", None)
    # Frozen from here: a moved estimate rewrites none of the payment's instalments.
    estimates.by_year[2025] = {"substitute_tax": D("3000.00")}
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert [i.amount for i in _items(db_session, entity) if i.source_kind == "tax_instalment"] == [
        D("166.67"),
        D("166.97"),
        D("167.51"),
    ]


def test_a_plan_chosen_after_the_first_instalments_day_keeps_what_was_already_late(
    db_session: Session, clock: Clock
) -> None:
    """The single June items exist and are overdue when the plan is chosen: they are replaced by
    every instalment, the late first one included, and removing the plan brings the single items
    back whatever their day. What was owed does not vanish because its date has passed."""
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    assert len(_balance_items(_items(db_session, entity))) == 1

    clock.today = dt.date(2026, 7, 10)
    _plan(db_session, entity, 3)
    balance = _balance_items(_items(db_session, entity))
    assert [(i.due_date, i.amount) for i in balance] == [
        (dt.date(2026, 6, 30), D("200.00")),
        (dt.date(2026, 7, 16), D("200.36")),
        (dt.date(2026, 8, 20), D("201.02")),
    ]

    _plan(db_session, entity, 1)
    assert [
        (i.source_kind, i.due_date, i.amount) for i in _balance_items(_items(db_session, entity))
    ] == [("tax_balance", dt.date(2026, 6, 30), D("600.00"))]


def test_a_carried_difference_stays_the_last_sentence_of_an_instalment() -> None:
    from pigrocrm.core.fiscal.tax_items import _instalment_text

    planned = split_into_instalments(
        D("100.00"),
        2,
        plan_schedule(IT_STANDARD_PACK, "irpef", 2025, (12, 31), "balance", dt.date(2026, 6, 30)),  # type: ignore[arg-type]
    )
    carry = ". Include +10,00 euro di differenza sull'acconto gia' pagato: da confermare"
    base = "Saldo IRPEF 2025" + carry
    assert _instalment_text(base, planned[1], 2) == (
        "Saldo IRPEF 2025, rateizzata: rata 2 di 2, con interessi dello 0,18%. "
        "Include +10,00 euro di differenza sull'acconto gia' pagato: da confermare"
    )


# --- a payment freezes the plan ----------------------------------------------------------


def _pay(session: Session, item: DueItem, amount: str) -> None:
    DueItemService(session).settle(
        item.id, SettlementCreate(amount=D(amount), settled_on=dt.date(2026, 5, 1)), ADMIN
    )


def test_a_partly_paid_instalment_keeps_its_remainder_and_nothing_is_re_split(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    _plan(db_session, entity, 3)
    first = _balance_items(_items(db_session, entity))[0]
    _pay(db_session, first, "50.00")

    detail = DueItemService(db_session).get(first.id, ADMIN)
    assert (detail.amount, detail.settled, detail.remaining, detail.state) == (
        D("200.00"),
        D("50.00"),
        D("150.00"),
        "expected",
    )
    snapshot = _rows(_items(db_session, entity))

    # The accountant's figure moves: the payment's instalments, paid or not, stay as created.
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert (report.written, report.dropped) == (0, 0)
    _declare(db_session, entity, tax_due="1000.00", balance="900.00")
    assert _rows(_items(db_session, entity)) == snapshot
    assert DueItemService(db_session).get(first.id, ADMIN).remaining == D("150.00")


def test_a_plan_with_a_payment_on_it_is_neither_changed_nor_removed(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    _plan(db_session, entity, 3)
    _pay(db_session, _balance_items(_items(db_session, entity))[0], "10.00")
    snapshot = _rows(_items(db_session, entity))

    for count in (2, 1):
        with pytest.raises(Conflict) as refused:
            _plan(db_session, entity, count)
        assert "pagamenti registrati" in str(refused.value)
    # Choosing the number already stored is not a change.
    assert [p.instalments for p in _plan(db_session, entity, 3).plans] == [3]
    assert _rows(_items(db_session, entity)) == snapshot
    assert [r.instalments for r in db_session.scalars(select(TaxInstalmentPlan))] == [3]


def test_a_payment_made_in_one_go_before_the_plan_is_left_as_it_is(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    single = _balance_items(_items(db_session, entity))[0]
    assert single.source_kind == "tax_balance"
    _pay(db_session, single, "600.00")

    _plan(db_session, entity, 3)
    items = _items(db_session, entity)
    # The balance was paid whole: it stays one settled item; the first advance is split.
    assert [(i.source_kind, i.state) for i in _balance_items(items)] == [("tax_balance", "settled")]
    assert len([i for i in items if i.source_kind == "tax_instalment"]) == 3


# --- who may ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "refusal"),
    [(COLLABORATOR, PermissionDenied), (READONLY, PermissionDenied), (AGENT, AgentForbidden)],
)
def test_the_plan_is_an_administrators_and_a_default_token_is_refused(
    db_session: Session, clock: Clock, actor: Actor, refusal: type[Exception]
) -> None:
    entity = _entity(db_session)
    _declare(db_session, entity, tax_due="1000.00", balance="600.00")
    with pytest.raises(refusal):
        _plan(db_session, entity, 3, actor=actor)
    assert db_session.scalars(select(TaxInstalmentPlan)).all() == []
    assert not [i for i in _items(db_session, entity) if i.source_kind == "tax_instalment"]
    # A token that carries `full_access` over an administrator may.
    assert _plan(db_session, entity, 3, actor=AGENT_FULL).plans[0].instalments == 3


def test_a_plan_needs_a_fiscal_profile(db_session: Session, clock: Clock) -> None:
    bare = LegalEntity(name="senza", legal_name="Senza srl", country="IT")
    db_session.add(bare)
    db_session.flush()
    with pytest.raises(NotFound):
        _plan(db_session, bare.id, 3)


# --- a notice from the tax office -------------------------------------------------------


def _notice(
    session: Session,
    entity: UUID,
    *,
    total: str = "1000.00",
    actor: Actor = ADMIN,
    instalments: list[tuple[dt.date, str]] | None = None,
) -> UUID:
    dates = instalments or [
        (dt.date(2026, 7, 10), "0.00"),
        (dt.date(2026, 8, 10), "2.50"),
        (dt.date(2026, 9, 10), "5.00"),
    ]
    return (
        TaxNoticePlanService(session)
        .create_tax_notice_plan(
            TaxNoticePlanSet(
                label="Avviso bonario 2023",
                tax="irpef",
                total=D(total),
                instalments=[NoticeInstalmentSet(due_date=d, interest=D(i)) for d, i in dates],
            ),
            actor,
            legal_entity_id=entity,
        )
        .id
    )


def test_a_notice_plan_writes_its_instalments_as_items_with_their_interest(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    plan_id = _notice(db_session, entity)

    items = [i for i in _items(db_session, entity) if i.source_kind == "notice_plan"]
    # 1000.00 / 3 = 333.33, 333.33 and 333.34, each with its own interest.
    assert [(i.due_date, i.amount) for i in items] == [
        (dt.date(2026, 7, 10), D("333.33")),
        (dt.date(2026, 8, 10), D("335.83")),
        (dt.date(2026, 9, 10), D("338.34")),
    ]
    assert {(i.state, i.direction, i.counterparty_type, i.tax) for i in items} == {
        ("expected", "out", "tax_office", "irpef")
    }
    assert items[1].description == "Avviso bonario 2023, rata 2 di 3, con 2,50 euro di interessi"
    # In the schedule, for an administrator alone.
    page = CashScheduleService(db_session).get_schedule(
        CashScheduleQuery(weeks=26, legal_entity_id=entity), ADMIN, modules=None, profile="solo"
    )
    assert page.taxes is not None
    assert [t.amount for t in page.taxes if "Avviso" in (t.description or "")] == [
        D("333.33"),
        D("335.83"),
        D("338.34"),
    ]
    stored = db_session.get(TaxNoticePlan, plan_id)
    assert stored is not None and stored.total == D("1000.00")

    listed = TaxNoticePlanService(db_session).list_tax_plans(ADMIN, legal_entity_id=entity)
    assert [p.id for p in listed.notice_plans] == [plan_id]
    assert [i.amount for i in listed.notice_plans[0].instalments] == [
        D("333.33"),
        D("335.83"),
        D("338.34"),
    ]


def test_a_notice_plan_is_changed_by_rewriting_its_items_and_deleted_with_them(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    plan_id = _notice(db_session, entity)
    service = TaxNoticePlanService(db_session)

    changed = service.update_tax_notice_plan(
        plan_id,
        TaxNoticePlanUpdate(
            total=D("600.00"),
            instalments=[
                NoticeInstalmentSet(due_date=dt.date(2026, 7, 10), interest=D("0.00")),
                NoticeInstalmentSet(due_date=dt.date(2026, 8, 10), interest=D("1.00")),
            ],
        ),
        ADMIN,
    )
    assert [i.amount for i in changed.instalments] == [D("300.00"), D("301.00")]
    live = [i for i in _items(db_session, entity) if i.source_kind == "notice_plan"]
    assert [i.amount for i in live] == [D("300.00"), D("301.00")]
    assert len(_items(db_session, entity, dropped=True)) == 3

    service.delete_tax_notice_plan(plan_id, ADMIN)
    assert [i for i in _items(db_session, entity) if i.source_kind == "notice_plan"] == []
    assert db_session.scalars(select(TaxNoticePlan)).all() == []
    with pytest.raises(NotFound):
        service.delete_tax_notice_plan(plan_id, ADMIN)


def test_a_notice_plan_with_a_payment_on_it_is_neither_changed_nor_deleted(
    db_session: Session, clock: Clock
) -> None:
    entity = _entity(db_session)
    plan_id = _notice(db_session, entity)
    first = [i for i in _items(db_session, entity) if i.source_kind == "notice_plan"][0]
    _pay(db_session, first, "100.00")
    service = TaxNoticePlanService(db_session)

    with pytest.raises(Conflict):
        service.update_tax_notice_plan(plan_id, TaxNoticePlanUpdate(label="Altro"), ADMIN)
    with pytest.raises(Conflict):
        service.delete_tax_notice_plan(plan_id, ADMIN)
    assert DueItemService(db_session).get(first.id, ADMIN).remaining == D("233.33")
    assert len([i for i in _items(db_session, entity) if i.source_kind == "notice_plan"]) == 3


def test_a_notice_plan_is_checked_before_it_is_written() -> None:
    with pytest.raises(ValueError, match="in ordine"):
        TaxNoticePlanSet(
            label="x",
            tax="irpef",
            total=D("10"),
            instalments=[
                NoticeInstalmentSet(due_date=dt.date(2026, 8, 10)),
                NoticeInstalmentSet(due_date=dt.date(2026, 7, 10)),
            ],
        )
    with pytest.raises(ValueError):
        TaxNoticePlanSet(label="x", tax="irpef", total=D("0"), instalments=[])


def test_a_total_too_small_to_split_is_refused(db_session: Session, clock: Clock) -> None:
    entity = _entity(db_session)
    with pytest.raises(ValidationFailed):
        _notice(
            db_session,
            entity,
            total="0.01",
            instalments=[(dt.date(2026, 7, 10), "0"), (dt.date(2026, 8, 10), "0")],
        )
    assert db_session.scalars(select(TaxNoticePlan)).all() == []


@pytest.mark.parametrize(
    ("actor", "refusal"),
    [(COLLABORATOR, PermissionDenied), (READONLY, PermissionDenied), (AGENT, AgentForbidden)],
)
def test_a_notice_plan_is_an_administrators_and_a_default_token_is_refused(
    db_session: Session, clock: Clock, actor: Actor, refusal: type[Exception]
) -> None:
    entity = _entity(db_session)
    with pytest.raises(refusal):
        _notice(db_session, entity, actor=actor)
    plan_id = _notice(db_session, entity)
    service = TaxNoticePlanService(db_session)
    with pytest.raises(refusal):
        service.update_tax_notice_plan(plan_id, TaxNoticePlanUpdate(label="x"), actor)
    with pytest.raises(refusal):
        service.delete_tax_notice_plan(plan_id, actor)
    with pytest.raises(refusal):
        service.list_tax_plans(actor, legal_entity_id=entity)
    assert len(db_session.scalars(select(TaxNoticePlan)).all()) == 1
    # `full_access` over an administrator may.
    assert _notice(db_session, entity, actor=AGENT_FULL)
