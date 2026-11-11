""" "All" is the sum of the legal entities, and the taxes are one legal entity's at a time
(REB-630, spec 2026-10-03 §1.9, §8 «Dashboard»).

Two legal entities, each with one customer, one open deal, one paid and one unpaid invoice and
some hours this week; a won deal, a per-deal cost and a shared cost on top. Every figure
below is a literal derived by hand from those rows, in the reading
`test_dashboard_economic.py` gives for doing so: an expectation recomputed the way the
code computes it agrees with a wrong implementation. The default legal entity is a
flat-rate one on the Italian pack, the second is a foreign company on `non-it`, so the
estimate and the ceiling have one legal entity they apply to and one they do not.

Committed sessions of their own, like every dashboard file: `DashboardService` sets the
isolation level as its first statement and Postgres refuses that once a transaction
has begun, so `db_session` cannot be used.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from frozen_clock import freeze_today
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import CeilingSimulationQuery, PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.dashboard.schemas import OperationalDashboard, PeriodQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.timetracking.models import CostCategory, TimeEntry

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=uuid7(), type="user", role="readonly")
_PREFIX = "DUEAZ"
TODAY = today_local()


@pytest.fixture(autouse=True)
def _pinned_today(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_today(monkeypatch, TODAY)


YEAR = TODAY.year
# One day of the year every invoice is dated and paid on: inside `YEAR` for the yearly
# sums, whichever month the suite runs in.
DAY = date(YEAR, 3, 10)
PERIOD = PeriodQuery(date_from=date(YEAR, 3, 1), date_to=date(YEAR, 3, 31))

# The figures, by hand, from the rows the fixture writes.
P_PAID = Decimal("1000.00")  # the studio's paid invoice
P_UNPAID = Decimal("200.00")  # the studio's unpaid one, already overdue
AND_PAID = Decimal("5000.00")
AND_UNPAID = Decimal("300.00")  # due in a month, not overdue
COST_SHARED = Decimal("50.00")  # no deal, no azienda: «tutte» alone
COST_AND = Decimal("20.00")  # on the foreign deal, so the foreign azienda's
HOURS_P = Decimal("2.00")
HOURS_AND = Decimal("3.00")


class Corpus(NamedTuple):
    engine: Engine
    studio: UUID
    foreign: UUID


def _require_empty(session: Session) -> None:
    for model in (Invoice, TimeEntry, Deal, Purchase):
        leftovers = session.execute(select(func.count(model.id))).scalar_one()
        assert leftovers == 0, (
            f"{leftovers} committed {model.__tablename__} row(s) were already present; the "
            "sums on these dashboards have no scope but the azienda, so leftovers change them."
        )


@pytest.fixture
def corpus(db_engine: Engine) -> Iterator[Corpus]:
    factory = session_factory(db_engine)
    with factory() as session:
        _require_empty(session)
        inserted = committed_default_legal_entity(session)
        studio = LegalEntityRepository(session).default()
        assert studio is not None
        foreign = LegalEntity(
            name=f"{_PREFIX} ltd", legal_name=f"{_PREFIX} Ltd", country="GB", is_default=False
        )
        open = PipelineStage(
            name=f"{_PREFIX} aperto", position=0, default_probability=20, type="open"
        )
        won = PipelineStage(
            name=f"{_PREFIX} vinto", position=1, default_probability=100, type="won"
        )
        user = User(
            email=f"{_PREFIX.lower()}-{uuid7()}@example.test",
            password_hash="x",
            name=f"{_PREFIX} operatore",
            role="collaborator",
        )
        session.add_all([foreign, open, won, user])
        session.flush()
        customer_p = Organization(
            legal_name=f"{_PREFIX} Cliente studio",
            country="IT",
            custom_fields={},
            legal_entity_id=studio.id,
        )
        customer_and = Organization(
            legal_name=f"{_PREFIX} Cliente estero",
            country="GB",
            custom_fields={},
            legal_entity_id=foreign.id,
        )
        session.add_all([customer_p, customer_and])
        session.flush()

        def _deal(name: str, customer: Organization, stage: PipelineStage, value: str) -> Deal:
            deal = Deal(
                name=f"{_PREFIX} {name}",
                customer_id=customer.id,
                legal_entity_id=customer.legal_entity_id,
                pipeline_stage_id=stage.id,
                probability=stage.default_probability,
                expected_value=Decimal(value),
                closed_at=DAY if stage.type == "won" else None,
                custom_fields={},
            )
            session.add(deal)
            session.flush()
            return deal

        def _invoice(
            customer: Organization,
            deal: Deal,
            *,
            number: int,
            amount: Decimal,
            paid: bool,
            due_date: date | None,
        ) -> None:
            session.add(
                Invoice(
                    customer_id=customer.id,
                    legal_entity_id=customer.legal_entity_id,
                    deal_id=deal.id,
                    type="invoice",
                    status="issued",
                    year=YEAR,
                    number=number,
                    payment_status="paid" if paid else "unpaid",
                    payment_date=DAY if paid else None,
                    taxable_amount=amount,
                    vat_amount=Decimal("0.00"),
                    stamp_duty=Decimal("0.00"),
                    total=amount,
                    issue_date=DAY,
                    due_date=due_date,
                    custom_fields={},
                )
            )

        def _hours(deal: Deal, hours: Decimal) -> None:
            session.add(
                TimeEntry(
                    deal_id=deal.id,
                    user_id=user.id,
                    date=TODAY,
                    hours=hours,
                    description="Lavorazione",
                    billable=True,
                    applied_rate=Decimal("50.000000"),
                    rate_source="manual",
                    applied_cost=None,
                    cost_source="absent",
                    custom_fields={},
                )
            )

        deal_p = _deal("studio aperto", customer_p, open, "1000.00")
        deal_e = _deal("estero aperto", customer_and, open, "3000.00")
        _deal("estero vinto", customer_and, won, "4000.00")
        _invoice(customer_p, deal_p, number=1, amount=P_PAID, paid=True, due_date=None)
        _invoice(
            customer_p,
            deal_p,
            number=2,
            amount=P_UNPAID,
            paid=False,
            due_date=TODAY - timedelta(days=10),
        )
        _invoice(customer_and, deal_e, number=1, amount=AND_PAID, paid=True, due_date=None)
        _invoice(
            customer_and,
            deal_e,
            number=2,
            amount=AND_UNPAID,
            paid=False,
            due_date=TODAY + timedelta(days=30),
        )
        _hours(deal_p, HOURS_P)
        _hours(deal_e, HOURS_AND)
        # Written by row, not through `CostCategoryService`: a service commits, and a
        # fixture that commits halfway leaves rows behind when a later step fails.
        category = CostCategory(name=f"{_PREFIX} categoria")
        session.add(category)
        session.flush()
        session.add_all(
            [
                Purchase(
                    deal_id=None,
                    legal_entity_id=None,
                    category_id=category.id,
                    date=DAY,
                    amount=COST_SHARED,
                    description=f"{_PREFIX} condiviso",
                    custom_fields={},
                ),
                Purchase(
                    deal_id=deal_e.id,
                    legal_entity_id=foreign.id,
                    category_id=category.id,
                    date=DAY,
                    amount=COST_AND,
                    description=f"{_PREFIX} estero",
                    custom_fields={},
                ),
            ]
        )
        session.flush()
        # By row, like the category above: the service commits, and a fixture that
        # commits halfway leaves rows behind when a later step fails. A foreign profile
        # with a zero rate needs a natura (REB-619); a British one charges VAT instead,
        # which is the shape REB-621's panel saves.
        session.add_all(
            [
                FiscalProfile(
                    **FiscalProfileUpsert(regime_code="RF19").model_dump(),
                    legal_entity_id=studio.id,
                ),
                FiscalProfile(
                    **FiscalProfileUpsert(
                        pack_id="non-it", default_vat_rate=Decimal("20.00")
                    ).model_dump(),
                    legal_entity_id=foreign.id,
                ),
            ]
        )
        session.commit()
        ids = Corpus(db_engine, studio.id, foreign.id)
    try:
        yield ids
    finally:
        with factory() as session:
            organizations = select(Organization.id).where(
                Organization.legal_name.like(f"{_PREFIX} %")
            )
            session.execute(
                delete(TimeEntry).where(
                    TimeEntry.deal_id.in_(select(Deal.id).where(Deal.name.like(f"{_PREFIX} %")))
                )
            )
            session.execute(delete(Purchase).where(Purchase.description.like(f"{_PREFIX} %")))
            session.execute(delete(CostCategory).where(CostCategory.name.like(f"{_PREFIX} %")))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(organizations)))
            session.execute(delete(Deal).where(Deal.name.like(f"{_PREFIX} %")))
            session.execute(
                delete(Organization).where(Organization.legal_name.like(f"{_PREFIX} %"))
            )
            session.execute(delete(PipelineStage).where(PipelineStage.name.like(f"{_PREFIX} %")))
            session.execute(delete(User).where(User.name == f"{_PREFIX} operatore"))
            session.execute(
                delete(FiscalProfile).where(
                    FiscalProfile.legal_entity_id.in_((ids.studio, ids.foreign))
                )
            )
            session.execute(delete(LegalEntity).where(LegalEntity.id == ids.foreign))
            remove_legal_entity(session, inserted)
            session.commit()


def _operational(engine: Engine, legal_entity_id: UUID | None) -> OperationalDashboard:
    with session_factory(engine)() as session:
        settings = Settings(_env_file=None, preferred_concentration_threshold=0.5)  # type: ignore[call-arg]
        return DashboardService(session, settings).get_operational_dashboard(
            READONLY, legal_entity_id
        )


def _signal(result: OperationalDashboard, code: str) -> int:
    return next(s.count for s in result.signals if s.code == code)


# --- the cash adds up, the shared cost only in "all" ------------------------------


def test_the_cash_overview_in_all_is_the_sum_of_the_legal_entities(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        all = service.cash_overview(YEAR, READONLY)
        studio = service.cash_overview(YEAR, READONLY, legal_entity_id=corpus.studio)
        foreign = service.cash_overview(YEAR, READONLY, legal_entity_id=corpus.foreign)
    assert (studio.paid, studio.unpaid) == (P_PAID, P_UNPAID)
    assert (foreign.paid, foreign.unpaid) == (AND_PAID, AND_UNPAID)
    assert all.paid == studio.paid + foreign.paid == Decimal("6000.00")
    assert all.unpaid == studio.unpaid + foreign.unpaid == Decimal("500.00")
    # The shared cost is nobody's: it is in «tutte» and in neither azienda (§1.7).
    assert (studio.costs, foreign.costs) == (Decimal("0.00"), COST_AND)
    assert all.costs == studio.costs + foreign.costs + COST_SHARED == Decimal("70.00")
    assert (all.legal_entity_id, studio.legal_entity_id, foreign.legal_entity_id) == (
        None,
        corpus.studio,
        corpus.foreign,
    )


def test_the_period_pnl_keeps_the_shared_cost_out_of_each_legal_entity(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        all = service.period_pnl(
            PeriodPnlQuery(date_from=PERIOD.date_from, date_to=PERIOD.date_to), READONLY
        )  # type: ignore[arg-type]
        foreign = service.period_pnl(
            PeriodPnlQuery(
                date_from=PERIOD.date_from, date_to=PERIOD.date_to, legal_entity_id=corpus.foreign
            ),  # type: ignore[arg-type]
            READONLY,
        )
    assert all.general_expenses == COST_SHARED
    assert foreign.general_expenses == Decimal("0.00")
    assert foreign.legal_entity_id == corpus.foreign
    # The foreign deal's own cost is per deal, not general, in both readings.
    assert all.in_progress.direct_costs == COST_AND == foreign.in_progress.direct_costs


# --- the taxes are one azienda's --------------------------------------------------


def test_the_estimate_needs_the_legal_entity_on_to_two_legal_entity_space(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        with pytest.raises(ValidationFailed) as refused:
            service.get_fiscal_estimate(YEAR, ADMIN)
        assert refused.value.details["field"] == "legal_entity_id"
        studio = service.get_fiscal_estimate(YEAR, ADMIN, legal_entity_id=corpus.studio)
    assert studio.legal_entity_id == corpus.studio
    # What the studio collected in the year (the flat-rate regime's cash basis), and none of the
    # foreign company's: its unpaid invoice is no revenue yet.
    assert studio.revenue == P_PAID


def test_the_overview_in_all_carries_no_estimate_and_names_each_share_s_legal_entity(
    corpus: Corpus,
) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        all = service.economic_overview(YEAR, ADMIN)
        studio = service.economic_overview(YEAR, ADMIN, legal_entity_id=corpus.studio)
    assert all.legal_entity_id is None
    assert all.tax is None and all.projected_tax is None
    assert all.actual_net is None
    # One customer per legal entity, each the whole of its own legal entity's revenue: a share
    # of the space would read 1/6 and 5/6 and hide exactly that.
    quote = {(row.legal_entity_id, row.legal_name): row.share for row in all.customer_concentration}
    assert quote == {
        (corpus.studio, f"{_PREFIX} Cliente studio"): 1.0,
        (corpus.foreign, f"{_PREFIX} Cliente estero"): 1.0,
    }
    assert studio.legal_entity_id == corpus.studio
    assert studio.tax is not None and studio.tax.legal_entity_id == corpus.studio
    assert studio.tax.revenue == P_PAID
    assert [row.legal_name for row in studio.customer_concentration] == [
        f"{_PREFIX} Cliente studio"
    ]


def test_the_ceiling_is_one_legal_entity_s_and_to_foreign_pack_has_none(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        with pytest.raises(ValidationFailed) as refused:
            service.ceiling_headroom(YEAR, READONLY)
        assert refused.value.details["field"] == "legal_entity_id"
        studio = service.ceiling_headroom(YEAR, READONLY, legal_entity_id=corpus.studio)
        foreign = service.ceiling_headroom(YEAR, READONLY, legal_entity_id=corpus.foreign)
        simulated = service.simulate_ceiling(
            YEAR,
            CeilingSimulationQuery(budgeted_value=Decimal("100.00")),
            READONLY,
            legal_entity_id=corpus.studio,
        )
    assert studio.legal_entity_id == corpus.studio
    # The studio's paid receipts alone: the foreign company's 5000 never reach its ceiling.
    assert {s.revenue for s in studio.thresholds} == {P_PAID}
    assert foreign.pack_id == "non-it" and foreign.thresholds == []
    assert simulated.legal_entity_id == corpus.studio
    assert {s.simulated_revenue for s in simulated.thresholds} == {Decimal("1100.00")}


# --- the four dashboards take the azienda as one more predicate --------------------


def test_the_commercial_dashboard_keeps_every_stage_and_sums_the_pipeline(
    corpus: Corpus,
) -> None:
    def _run(legal_entity_id: UUID | None) -> dict[str, tuple[int, Decimal]]:
        with session_factory(corpus.engine)() as session:
            result = DashboardService(session).get_commercial_dashboard(
                PeriodQuery(
                    date_from=PERIOD.date_from,
                    date_to=PERIOD.date_to,
                    legal_entity_id=legal_entity_id,
                ),
                READONLY,
            )
        assert result.legal_entity_id == legal_entity_id
        return {
            row.stage_name: (row.number, row.total_value)
            for row in result.pipeline
            if row.stage_name.startswith(_PREFIX)
        }

    all, studio, foreign = _run(None), _run(corpus.studio), _run(corpus.foreign)
    assert all == {
        f"{_PREFIX} aperto": (2, Decimal("4000.00")),
        f"{_PREFIX} vinto": (1, Decimal("4000.00")),
    }
    # A stage with none of the studio's deals is still a row, at zero: the outer join
    # keeps it, which a `where` on the deal's azienda would silently have undone.
    assert studio == {
        f"{_PREFIX} aperto": (1, Decimal("1000.00")),
        f"{_PREFIX} vinto": (0, Decimal("0.00")),
    }
    assert foreign == {
        f"{_PREFIX} aperto": (1, Decimal("3000.00")),
        f"{_PREFIX} vinto": (1, Decimal("4000.00")),
    }


def test_the_closures_and_the_receivables_follow_the_legal_entity(corpus: Corpus) -> None:
    def _closures(legal_entity_id: UUID | None) -> int:
        with session_factory(corpus.engine)() as session:
            return (
                DashboardService(session)
                .get_commercial_dashboard(
                    PeriodQuery(
                        date_from=PERIOD.date_from,
                        date_to=PERIOD.date_to,
                        legal_entity_id=legal_entity_id,
                    ),
                    READONLY,
                )
                .closings.won
            )

    def _receivables(legal_entity_id: UUID | None) -> tuple[Decimal, Decimal]:
        with session_factory(corpus.engine)() as session:
            result = DashboardService(session).get_receivables_dashboard(READONLY, legal_entity_id)
        assert result.legal_entity_id == legal_entity_id
        expired = next(f.amount for f in result.bands if f.code == "expired")
        return result.total, expired

    assert (_closures(None), _closures(corpus.studio), _closures(corpus.foreign)) == (1, 0, 1)
    assert _receivables(corpus.studio) == (P_UNPAID, P_UNPAID)
    assert _receivables(corpus.foreign) == (AND_UNPAID, Decimal("0.00"))
    assert _receivables(None) == (Decimal("500.00"), P_UNPAID)


def test_the_operational_dashboard_narrows_the_week_the_backlog_and_the_signals(
    corpus: Corpus,
) -> None:
    all = _operational(corpus.engine, None)
    studio = _operational(corpus.engine, corpus.studio)
    foreign = _operational(corpus.engine, corpus.foreign)
    assert (studio.week.total_hours, foreign.week.total_hours) == (HOURS_P, HOURS_AND)
    assert all.week.total_hours == HOURS_P + HOURS_AND
    assert (studio.backlog.accrued_value, foreign.backlog.accrued_value) == (
        Decimal("100.00"),
        Decimal("150.00"),
    )
    assert all.backlog.accrued_value == Decimal("250.00")
    assert [_signal(r, "overdue_unpaid") for r in (all, studio, foreign)] == [1, 1, 0]
    # Each customer is the whole of its own azienda, so above a 50% threshold in both;
    # «tutte» counts the two of them, never a space-wide 1/6 that would hide the studio's.
    assert [_signal(r, "concentration_above_threshold") for r in (all, studio, foreign)] == [
        2,
        1,
        1,
    ]
    assert (all.legal_entity_id, studio.legal_entity_id, foreign.legal_entity_id) == (
        None,
        corpus.studio,
        corpus.foreign,
    )


# --- the two cases the review of REB-630 asked for ------------------------------------


def test_to_deactivated_legal_entity_s_cash_never_reaches_the_active_one_s_estimate(
    corpus: Corpus,
) -> None:
    """With one active legal entity the overview in "all" still adds up a deactivated
    legal entity's receipts, which must not reach the active one's coefficients: the estimate
    and the two nets are read off the active legal entity's own cash (spec §1.9)."""
    factory = session_factory(corpus.engine)
    with factory() as session:
        LegalEntityService(session).deactivate(corpus.foreign, ADMIN)
    try:
        with factory() as session:
            all = AnalyticsService(session).economic_overview(YEAR, ADMIN)
        # The cash is every azienda's, the deactivated one included.
        assert all.pension_fund.paid == P_PAID + AND_PAID
        assert all.tax is not None and all.tax.legal_entity_id == corpus.studio
        assert all.tax.revenue == P_PAID
        assert all.tax.due_total is not None
        # The net is the studio's own lordo less its own taxes, not the page's lordo.
        assert all.actual_net == P_PAID - all.tax.due_total
    finally:
        with factory() as session:
            row = session.get(LegalEntity, corpus.foreign)
            assert row is not None
            row.active = True
            session.commit()


def test_the_concentration_signal_counts_to_customer_billed_by_two_legal_entities_once(
    corpus: Corpus,
) -> None:
    """A customer over the threshold in both legal entities is two rows of the concentration
    table, one share per legal entity, and one person to talk to on the signal."""
    factory = session_factory(corpus.engine)
    with factory() as session:
        customer = session.execute(
            select(Organization).where(Organization.legal_name == f"{_PREFIX} Cliente studio")
        ).scalar_one()
        # The studio's customer, billed by the foreign company for more than everything
        # the foreign company billed its own customer: over 50% on both sides.
        extra = Invoice(
            customer_id=customer.id,
            legal_entity_id=corpus.foreign,
            deal_id=None,
            type="invoice",
            status="issued",
            year=YEAR,
            number=3,
            payment_status="paid",
            payment_date=DAY,
            taxable_amount=Decimal("6000.00"),
            vat_amount=Decimal("0.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal("6000.00"),
            issue_date=DAY,
            custom_fields={},
        )
        session.add(extra)
        session.commit()
        extra_id = extra.id
    try:
        with factory() as session:
            rows = AnalyticsRepository(session).revenue_by_customer(YEAR)
            both = [r for r in rows if r.customer_id == customer.id]
            assert {r.legal_entity_id for r in both} == {corpus.studio, corpus.foreign}
            assert all(r.share > 0.5 for r in both)
            # The foreign company's own customer fell under the threshold: 5000 of 11000.
            assert AnalyticsRepository(session).count_over_concentration_threshold(YEAR, 0.5) == 1
        all_legal_entities = _operational(corpus.engine, None)
        assert _signal(all_legal_entities, "concentration_above_threshold") == 1
    finally:
        with factory() as session:
            session.execute(delete(Invoice).where(Invoice.id == extra_id))
            session.commit()


def test_the_invoiced_not_won_signal_follows_the_deal_s_legal_entity_like_its_list(
    corpus: Corpus,
) -> None:
    """The card counts deals and its drill-through lists deals by `Deal.legal_entity_id`, so
    an invoice that sits on another azienda than its deal's (an import can do that)
    must not make the deal count under the invoice's azienda."""
    factory = session_factory(corpus.engine)
    with factory() as session:
        deal = session.execute(
            select(Deal).where(Deal.name == f"{_PREFIX} studio aperto")
        ).scalar_one()
        stray = Invoice(
            customer_id=deal.customer_id,
            legal_entity_id=corpus.foreign,
            deal_id=deal.id,
            type="invoice",
            status="issued",
            year=YEAR,
            number=4,
            payment_status="unpaid",
            taxable_amount=Decimal("10.00"),
            vat_amount=Decimal("0.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal("10.00"),
            issue_date=DAY,
            custom_fields={},
        )
        session.add(stray)
        session.commit()
        stray_id = stray.id
    try:
        studio = _operational(corpus.engine, corpus.studio)
        foreign = _operational(corpus.engine, corpus.foreign)
        # The studio's open deal is invoiced either way; the foreign company's open deal
        # is invoiced too, by its own paid invoice. The stray invoice adds no deal to
        # the foreign company's count: the deal it sits on is the studio's.
        assert _signal(studio, "invoiced_unwon") == 1
        assert _signal(foreign, "invoiced_unwon") == 1
        with session_factory(corpus.engine)() as session:
            counted = DealRepository(session).list(
                DealListQuery(invoiced_unwon=True, legal_entity_id=corpus.foreign, limit=50)
            )
        assert [d.name for d in counted] == [f"{_PREFIX} estero aperto"]
    finally:
        with factory() as session:
            session.execute(delete(Invoice).where(Invoice.id == stray_id))
            session.commit()
