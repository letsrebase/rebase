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

from pigrocrm.core.activities.models import Activity
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
from pigrocrm.core.errors import AgentForbidden, Conflict, PermissionDenied
from pigrocrm.core.fiscal import tax_items
from pigrocrm.core.fiscal.advance_method import TaxAdvanceMethodService
from pigrocrm.core.fiscal.advance_schemas import (
    TaxAdvanceMethodRead,
    TaxAdvanceMethodSet,
    TaxItemsReport,
)
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
            dt.date(2026, 7, 20),
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
    assert [(n.tax, n.year, n.part) for n in report.not_written if n.tax != "inps"] == [
        ("substitute_tax", 2025, "balance")
    ]
    assert "no advance rule" in report.not_written[0].reason

    chosen = _method(db_session, entity, 2026, "forecast")
    assert (chosen.year, chosen.method) == (2026, "forecast")
    assert (chosen.items.written, chosen.items.dropped) == (2, 2)
    assert [(r.due, r.amount, r.state) for r in _live(db_session, entity)] == [
        (dt.date(2026, 7, 20), D("1500.00"), "forecast"),
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
    reasons = {(n.year, n.part): n.reason for n in chosen.items.not_written if n.tax != "inps"}
    assert (
        "forecast method needs the imposta sostitutiva estimate of 2026"
        in reasons[(2026, "first_advance")]
    )


def test_an_administrator_confirms_the_difference_carried_onto_a_tax_item(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    """REB-1079: the item says it is waiting, only an administrator confirms it, the amount stays,
    the confirmation survives a recomputation and a new difference asks again."""
    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="1000.00")
    _pay(db_session, entity, dt.date(2026, 7, 20), "500.00")
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="1200.00")
    service = DueItemService(db_session)
    item = db_session.scalars(
        select(DueItem).where(
            DueItem.legal_entity_id == entity,
            DueItem.source_kind == "tax_advance",
            DueItem.state == "expected",
        )
    ).one()
    assert service.get(item.id, ADMIN).carry_to_confirm is True

    for refused in (COLLABORATOR, READONLY):
        with pytest.raises(PermissionDenied):
            service.confirm_tax_carry(item.id, refused)
    # A decision for a person: no agent credential takes it, a `full_access` one included.
    for agent in (AGENT, AGENT_FULL):
        with pytest.raises(AgentForbidden):
            service.confirm_tax_carry(item.id, agent)

    confirmed = service.confirm_tax_carry(item.id, ADMIN)
    assert confirmed.carry_to_confirm is False
    assert confirmed.amount == D("700.00")
    # Twice is the same answer, and one entry in the timeline.
    assert service.confirm_tax_carry(item.id, ADMIN).carry_to_confirm is False
    assert (
        len(
            db_session.scalars(
                select(Activity).where(
                    Activity.entity_type == "legal_entity", Activity.kind == "tax_carry_confirmed"
                )
            ).all()
        )
        == 1
    )
    # A recomputation with nothing new leaves the item and its confirmation alone.
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert service.get(item.id, ADMIN).carry_to_confirm is False
    # The difference moves: the item is rewritten and asks again.
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="900.00")
    rewritten = db_session.scalars(
        select(DueItem).where(
            DueItem.legal_entity_id == entity,
            DueItem.source_kind == "tax_advance",
            DueItem.state == "expected",
            DueItem.due_date == dt.date(2026, 11, 30),
        )
    ).one()
    assert service.get(rewritten.id, ADMIN).carry_to_confirm is True
    # A plain item has nothing to confirm.
    first = db_session.scalars(
        select(DueItem).where(
            DueItem.legal_entity_id == entity,
            DueItem.due_date == dt.date(2026, 7, 20),
            DueItem.state == "settled",
        )
    ).one()
    with pytest.raises(Conflict):
        service.confirm_tax_carry(first.id, ADMIN)


def test_the_estimate_says_which_method_the_advances_of_its_year_use(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    """REB-1079: «Tasse» shows the method in force beside the year's taxes."""
    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    estimates.set(2026, "substitute_tax", "3000.00")

    def method(year: int) -> str:
        return (
            AnalyticsService(db_session)
            .get_fiscal_estimate(year, ADMIN, legal_entity_id=entity)
            .advance_method
        )

    assert method(2026) == "historical"
    _method(db_session, entity, 2026, "forecast")
    assert method(2026) == "forecast"
    assert method(2025) == "historical"


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
        (dt.date(2026, 7, 20), D("500.00"), "expected"),
        (dt.date(2026, 11, 30), D("500.00"), "expected"),
    ]
    assert {i.state for i in _dropped(db_session, entity)} == {"dropped"}
    assert len(_dropped(db_session, entity)) == 2

    # The first advance is paid at 500. The return is corrected to 1200: the advance should have
    # been 600, the 100 it was short is owed with the second one, which a person is asked to
    # confirm.
    _pay(db_session, entity, dt.date(2026, 7, 20), "500.00")
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
    _pay(db_session, entity, dt.date(2026, 7, 20), "450.00")
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
    _pay(db_session, entity, dt.date(2026, 7, 20), "200.00")  # partly paid
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="2000.00")
    first = _live(db_session, entity)[0]
    # Partly paid: it keeps its amount and its remainder, with no re-split.
    assert (first.amount, first.state) == (D("500.00"), "expected")
    # Nothing owed any more: the unpaid item goes, the partly paid one stays.
    _declare(db_session, entity, "substitute_tax", 2025, tax_due="10.00")
    assert [(r.due, r.amount) for r in _live(db_session, entity)] == [
        (dt.date(2026, 7, 20), D("500.00"))
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
        (dt.date(2026, 7, 20), D("400.00")),
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
        (dt.date(2026, 7, 20), D("103.01")),
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
    # An IRAP advance of 100 has a first instalment of 40, not above 103: one payment (art. 17
    # c. 3 D.P.R. 435/2001), the same test as IRES.
    assert [(r.tax, r.due, r.amount) for r in rows] == [
        ("ires", dt.date(2026, 7, 20), D("4000.00")),
        ("irap", dt.date(2026, 11, 30), D("100.00")),
        ("ires", dt.date(2026, 11, 30), D("6000.00")),
    ]
    # The 103 is a ceiling on the first instalment: an IRES advance of 200 (first 80) is one
    # payment of 200, not 80 and 120; 20.66 or less is none at all.
    _declare(db_session, entity, "ires", 2025, tax_due="200.00")
    _declare(db_session, entity, "irap", 2025, tax_due="20.66")
    assert [(r.tax, r.due, r.amount) for r in _live(db_session, entity)] == [
        ("ires", dt.date(2026, 11, 30), D("200.00"))
    ]
    # Above 257.51 the first instalment passes 103 and the advance is split again.
    _declare(db_session, entity, "ires", 2025, tax_due="257.52")
    assert [(r.tax, r.due, r.amount) for r in _live(db_session, entity)] == [
        ("ires", dt.date(2026, 7, 20), D("103.01")),
        ("ires", dt.date(2026, 11, 30), D("154.51")),
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
    assert all(
        "ISA" in n.reason for n in report.not_written if n.part != "balance" and n.tax != "inps"
    )


def test_a_tax_with_no_estimate_and_no_declared_figure_has_no_item_and_says_so(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-standard", isa=False)
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert _live(db_session, entity) == []
    reasons = {(n.year, n.part): n.reason for n in report.not_written if n.tax != "inps"}
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
        (dt.date(2026, 7, 20), D("4000.00")),
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
        (dt.date(2026, 7, 20), D("500.00"), "forecast"),
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


def test_a_forecast_tax_item_can_be_paid_and_then_keeps_what_was_paid(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    """No document replaces a tax's forecast, so paying one makes it the item that was paid: it
    freezes and the carry then sees the real payment."""
    entity = _entity(db_session)
    estimates.set(2025, "substitute_tax", "1000.00")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    item = db_session.scalars(
        select(DueItem)
        .where(DueItem.source_kind == "tax_advance", DueItem.state == "forecast")
        .order_by(DueItem.due_date)
    ).first()
    assert item is not None
    DueItemService(db_session).settle(
        item.id, SettlementCreate(amount=item.amount, settled_on=dt.date(2026, 5, 1)), ADMIN
    )
    estimates.set(2025, "substitute_tax", "1200.00")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    first, second = _live(db_session, entity)
    assert (first.amount, first.state) == (D("500.00"), "settled")
    assert (second.amount, second.state) == (D("700.00"), "forecast")
    assert "da confermare" in (second.text or "")


def test_the_method_needs_a_fiscal_profile(db_session: Session, clock: Clock) -> None:
    from pigrocrm.core.errors import NotFound

    entity = LegalEntity(name="senza profilo", legal_name="Senza profilo", country="IT")
    db_session.add(entity)
    db_session.flush()
    with pytest.raises(NotFound):
        _method(db_session, entity.id, 2026, "forecast")


# --- the srl's chamber of commerce fee (REB-1077) ---------------------------------------------


def _fee_rows(session: Session, entity: UUID) -> list[DueItem]:
    session.expire_all()
    return list(
        session.scalars(
            select(DueItem).where(
                DueItem.legal_entity_id == entity,
                DueItem.source_kind == "chamber_fee",
                DueItem.state != "dropped",
            )
        )
    )


def _refresh(session: Session, entity: UUID, province: str | None) -> TaxItemsReport:
    row = session.get(LegalEntity, entity)
    assert row is not None
    row.province = province
    session.flush()
    return TaxDueItemService(session).refresh_tax_due_items(entity, today=dt.date(2026, 5, 15))


def test_an_srl_in_milano_with_small_turnover_has_the_fee_on_the_first_advance_day(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-company", isa=False)
    report = _refresh(db_session, entity, "MI")
    (item,) = _fee_rows(db_session, entity)
    assert (item.amount, item.due_date, item.state, item.tax) == (
        D("120.00"),
        dt.date(2026, 7, 20),
        "forecast",
        "chamber_fee",
    )
    assert item.counterparty_type == "tax_office"
    assert "3850" in (item.description or "")
    assert not any(n.part == "chamber_fee" for n in report.not_written)
    # Idempotent.
    _refresh(db_session, entity, "MI")
    assert len(_fee_rows(db_session, entity)) == 1


def test_napoli_adds_no_increase(db_session: Session, clock: Clock, estimates: Estimates) -> None:
    entity = _entity(db_session, "it-company", isa=False)
    _refresh(db_session, entity, "NA")
    (item,) = _fee_rows(db_session, entity)
    assert item.amount == D("100.00")


@pytest.mark.parametrize("province", [None, "", "OG", "OT"])
def test_a_missing_or_unmapped_province_gives_no_fee_and_says_why(
    db_session: Session, clock: Clock, estimates: Estimates, province: str | None
) -> None:
    entity = _entity(db_session, "it-company", isa=False)
    report = _refresh(db_session, entity, province)
    assert _fee_rows(db_session, entity) == []
    reasons = [n.reason for n in report.not_written if n.part == "chamber_fee"]
    assert len(reasons) == 1


def test_a_sole_trader_has_no_chamber_fee(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-flat-rate")
    _refresh(db_session, entity, "MI")
    assert _fee_rows(db_session, entity) == []


def test_a_chamber_fee_can_be_paid_and_then_keeps_what_was_paid(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-company", isa=False)
    _refresh(db_session, entity, "MI")
    (item,) = _fee_rows(db_session, entity)
    DueItemService(db_session).settle(
        item.id, SettlementCreate(amount=D("120.00"), settled_on=dt.date(2026, 5, 20)), ADMIN
    )
    _refresh(db_session, entity, "MI")
    (kept,) = _fee_rows(db_session, entity)
    assert (kept.id, kept.state, kept.amount) == (item.id, "settled", D("120.00"))


def test_an_srl_in_a_year_without_a_chamber_fee_law_has_no_item_and_says_so(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    """The fee is a yearly figure (REB-1083): in 2027 the 2026 law is not read again."""
    entity = _entity(db_session, "it-company", isa=False)
    row = db_session.get(LegalEntity, entity)
    assert row is not None
    row.province = "MI"
    db_session.flush()
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=dt.date(2027, 5, 15))
    assert _fee_rows(db_session, entity) == []
    reasons = [n.reason for n in report.not_written if n.part == "chamber_fee" and n.year == 2027]
    assert len(reasons) == 1 and "2027" in reasons[0]


# --- the advances of a person's INPS contributions (REB-1097) -----------------------------------


class InpsIncomes:
    """The estimated income of each year the stub answers; a year not set has none."""

    def __init__(self) -> None:
        self.by_year: dict[int, Decimal | None] = {}


@pytest.fixture
def inps_incomes(monkeypatch: pytest.MonkeyPatch) -> InpsIncomes:
    stub = InpsIncomes()

    def estimated_inps_income(
        self: AnalyticsService, year: int, legal_entity: LegalEntity, profile: object
    ) -> Decimal | None:
        return stub.by_year.get(year)

    monkeypatch.setattr(AnalyticsService, "estimated_inps_income", estimated_inps_income)
    return stub


def _inps_entity(session: Session, pack_id: str, **facts: object) -> UUID:
    entity = _entity(session, pack_id, isa=False)
    profile = session.scalars(
        select(FiscalProfile).where(FiscalProfile.legal_entity_id == entity)
    ).one()
    for key, value in facts.items():
        setattr(profile, key, value)
    session.flush()
    return entity


def _inps_rows(session: Session, entity: UUID) -> list[Row]:
    return [row for row in _live(session, entity) if row.tax == "inps"]


def test_the_inps_advances_are_two_equal_halves_of_the_excess_on_last_years_income(
    db_session: Session, clock: Clock, estimates: Estimates, inps_incomes: InpsIncomes
) -> None:
    """Redditi PF 2026 appendix «INPS»: a trader's 2025 income of 30.000 is 7.344,00 at 24,48%,
    less 4.604,20 on the 2026 minimum = 2.739,80, on the days of the income tax advances."""
    entity = _inps_entity(
        db_session,
        "it-flat-rate",
        inps_category="artigiani_commercianti",
        inps_trade="commercianti",
        flat_rate_inps_reduction=False,
    )
    inps_incomes.by_year[2025] = D("30000")
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert _inps_rows(db_session, entity) == [
        Row(
            "tax_advance",
            "inps",
            dt.date(2026, 6, 30),
            D("1369.90"),
            "forecast",
            "Acconto INPS 2026, 1a rata",
        ),
        Row(
            "tax_advance",
            "inps",
            dt.date(2026, 11, 30),
            D("1369.90"),
            "forecast",
            "Acconto INPS 2026, 2a rata",
        ),
    ]
    # 2027 has no INPS law yet, so the pack gives it no item and the report does not ask for one.
    assert not any(n.tax == "inps" and n.year == 2027 for n in report.not_written)


def test_the_flat_rate_reduction_is_in_the_inps_advances_and_unanswered_it_gives_none(
    db_session: Session, clock: Clock, estimates: Estimates, inps_incomes: InpsIncomes
) -> None:
    entity = _inps_entity(
        db_session,
        "it-flat-rate",
        inps_category="artigiani_commercianti",
        inps_trade="commercianti",
    )
    inps_incomes.by_year[2025] = D("30000")
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert _inps_rows(db_session, entity) == []
    reasons = {n.reason for n in report.not_written if n.tax == "inps" and n.year == 2026}
    assert any("35%" in reason for reason in reasons)

    profile = db_session.scalars(
        select(FiscalProfile).where(FiscalProfile.legal_entity_id == entity)
    ).one()
    profile.flat_rate_inps_reduction = True
    db_session.flush()
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    # 2.739,80 x 65% = 1.780,87, the first advance rounded up.
    assert [row.amount for row in _inps_rows(db_session, entity)] == [D("890.44"), D("890.43")]


def test_inps_advances_without_last_years_income_or_a_category_give_none_and_say_why(
    db_session: Session, clock: Clock, estimates: Estimates, inps_incomes: InpsIncomes
) -> None:
    entity = _inps_entity(
        db_session, "it-standard", inps_category="gestione_separata", inps_covered_elsewhere=False
    )
    report = TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    assert _inps_rows(db_session, entity) == []
    assert any(
        n.tax == "inps" and n.year == 2026 and "income of 2025" in n.reason
        for n in report.not_written
    )
    inps_incomes.by_year[2025] = D("40000")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    # 40.000 x 80% x 26,07% = 8.342,40, two advances.
    assert [row.amount for row in _inps_rows(db_session, entity)] == [D("4171.20")] * 2


def test_a_paid_first_inps_advance_is_kept_and_the_second_takes_what_the_total_leaves(
    db_session: Session, clock: Clock, estimates: Estimates, inps_incomes: InpsIncomes
) -> None:
    entity = _inps_entity(
        db_session, "it-standard", inps_category="gestione_separata", inps_covered_elsewhere=False
    )
    inps_incomes.by_year[2025] = D("40000")
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    first = db_session.scalars(
        select(DueItem).where(
            DueItem.legal_entity_id == entity,
            DueItem.tax == "inps",
            DueItem.due_date == dt.date(2026, 6, 30),
        )
    ).one()
    DueItemService(db_session).settle(
        first.id, SettlementCreate(amount=D("4171.20"), settled_on=dt.date(2026, 5, 1)), ADMIN
    )
    inps_incomes.by_year[2025] = D("50000")  # 50.000 x 80% x 26,07% = 10.428,00
    TaxDueItemService(db_session).refresh_tax_due_items(entity, today=clock.today)
    rows = _inps_rows(db_session, entity)
    assert [(row.due, row.amount, row.state) for row in rows] == [
        (dt.date(2026, 6, 30), D("4171.20"), "settled"),
        (dt.date(2026, 11, 30), D("6256.80"), "forecast"),
    ]


def _register(session: Session, entity: UUID, applied: dt.date) -> None:
    row = session.get(LegalEntity, entity)
    assert row is not None
    row.registration_date = applied
    session.flush()


def test_a_company_registered_this_year_owes_the_fixed_amount_within_thirty_days(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    """REB-1100: the application of 10 May 2026 makes the fee of 2026 the first tier's 120
    euro in a 20% chamber, due 30 days after it (9 June, a Tuesday), and not a forecast on
    the turnover of 2025 on 30 June."""
    entity = _entity(db_session, "it-company", isa=False)
    _register(db_session, entity, dt.date(2026, 5, 10))
    _refresh(db_session, entity, "MI")
    (item,) = _fee_rows(db_session, entity)
    assert (item.amount, item.due_date, item.state) == (
        D("120.00"),
        dt.date(2026, 6, 9),
        "expected",
    )
    assert "3850" in (item.description or "")
    assert "10/05/2026" in (item.description or "")


def test_the_thirty_days_move_to_the_next_working_day(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    """30 days after 21 May 2026 is Saturday 20 June: the deadline is Monday 22 June."""
    entity = _entity(db_session, "it-company", isa=False)
    _register(db_session, entity, dt.date(2026, 5, 21))
    _refresh(db_session, entity, "MI")
    (item,) = _fee_rows(db_session, entity)
    assert item.due_date == dt.date(2026, 6, 22)


def test_the_year_after_the_registration_reads_the_turnover_again(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-company", isa=False)
    _register(db_session, entity, dt.date(2025, 11, 20))
    _refresh(db_session, entity, "MI")
    (item,) = _fee_rows(db_session, entity)
    assert (item.due_date, item.state) == (dt.date(2026, 7, 20), "forecast")


def test_an_unmapped_province_gives_no_first_year_fee_and_says_why(
    db_session: Session, clock: Clock, estimates: Estimates
) -> None:
    entity = _entity(db_session, "it-company", isa=False)
    _register(db_session, entity, dt.date(2026, 5, 10))
    report = _refresh(db_session, entity, "OG")
    assert _fee_rows(db_session, entity) == []
    assert len([n for n in report.not_written if n.part == "chamber_fee"]) == 1
