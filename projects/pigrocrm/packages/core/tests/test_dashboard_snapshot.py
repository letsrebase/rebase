"""**Criterion 6.** A dashboard is one instant, and this is what proves it.

`test_dashboard_commercial.py` already shows a commit landing between two of the
dashboard's *aggregates* and staying invisible. This file goes at the same property from
the other side, and adds the three things that file cannot say:

  (a) the constant `SNAPSHOT_ISOLATION` is the level the connection actually gets --
      asserted for both values, so the lever the inversion below pulls is proven to be
      connected to something before it is used as evidence;
  (b) a genuinely parallel connection COMMITs *between* `_open_snapshot()` and the very
      first aggregate, synchronised with two events rather than by calling the writer
      inline -- so what is asserted is that the snapshot is fixed by the
      `transaction_timestamp()` statement itself, not merely by whichever query ran first;
  (c) `calcolato_alle` precedes that commit, bracketed by the database's own clock.

And then the inversion, which is what makes (a) mean something instead of decorating the
file: repeated with the isolation level forced to `read committed`, (b) **fails** -- the
intruder shows up in two independent figures. `transaction_timestamp()` is constant for a
whole transaction even in `READ COMMITTED`, so a test that stopped at (c) would pass on a
dashboard that read seven different states. A control test then re-reads the dashboard
afterwards and requires the intruder to be visible, so that (b) cannot pass because the
writer quietly failed and the row never existed at all.

**Criterion 6 is a criterion for every dashboard, not for the first one built.** Sub-plan 6C
adds two more, so the machinery above is parametrised over all three: clause (a) asserts the
level on each, and clause (b) runs each one against the intruder its own figures could
actually leak. The barrier therefore hangs off whichever aggregate each dashboard calls
first -- `pipeline_summary`, `period_pnl`, `week_hours` -- and the intruder differs too: a
deal moves no figure on the economic page, and an invoice moves none on the commercial one,
so one shared intruder would have made two of the three runs vacuous.

Clause (b)'s expectation is the dashboard read *before* the barrier run, not a literal.
Every figure the two new dashboards leak into -- the receivable, the count of issued
invoices, the two operational signals -- is a whole-register aggregate with no period and no
prefix to scope it, so a row another test file committed and has not yet torn down would
move a literal and prove nothing. Comparing against a baseline taken on the same engine
moments earlier says exactly what the criterion says: this commit changed none of them.

This file deliberately does not use the `db_session` fixture: it holds an outer transaction
open, and Postgres refuses to change the isolation level once a transaction has begun.
`test_dashboard_commercial.py` is the other dashboard file that builds its own sessions,
and for the same reason. Like that file, the teardown empties `pipeline_stages` wholesale --
`db_engine` is session-scoped, and six stages left behind would be six stages every other
test in the suite did not create.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from fakes.azienda_fixtures import committed_default_azienda, remove_azienda
from sqlalchemy import Engine, delete, select, text

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard import service as dashboard_service
from pigrocrm.core.dashboard.schemas import (
    CommercialDashboard,
    EconomicDashboard,
    OperationalDashboard,
    PeriodoQuery,
    PipelineStageSummary,
)
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds, session_factory, today_local, window_from
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.timetracking.repository import TimeEntryRepository

READONLY = Actor(id=uuid7(), type="user", role="readonly")
SEED = Actor(id=None, type="system", role="admin")
_PREFIX = "SNAP"
# Generous on purpose: this bounds a deadlock, not a query. A barrier that times out
# reports "the writer never committed" rather than hanging the suite.
_BARRIER_TIMEOUT = 30.0


class Seeded(NamedTuple):
    engine: Engine
    customer_id: UUID
    stages: dict[str, PipelineStageRead]
    # The base deal, on an `open` stage. The invoice intruder hangs off it: `revenue_in_range`
    # groups by `deal_id` and skips nulls, so an unlinked invoice would reach no P&L column
    # at all and clause (b) would be asserting that an invisible row stayed invisible.
    deal_id: UUID


@pytest.fixture
def seeded(db_engine: Engine) -> Iterator[Seeded]:
    factory = session_factory(db_engine)
    with factory() as session:
        azienda_id = committed_default_azienda(session)
        stages = {s.code: s for s in PipelineService(session).seed_defaults(SEED) if s.code}
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        session.add(customer)
        session.flush()
        customer_id = customer.id
        # No `data_chiusura_prevista`: the baseline for "prossime chiusure" has to be zero
        # so that the intruder below is the only thing that could ever move it.
        base = Deal(
            nome=f"{_PREFIX} base",
            customer_id=customer_id,
            pipeline_stage_id=stages["lead"].id,
            valore_previsto=Decimal("1000.00"),
            probabilita=50,
            custom_fields={},
        )
        session.add(base)
        session.flush()
        deal_id = base.id
        session.commit()
    try:
        yield Seeded(db_engine, customer_id, stages, deal_id)
    finally:
        with factory() as session:
            # Before the deals: the invoice intruder carries a foreign key to one. Scoped to
            # this file's own customer rather than a wholesale `DELETE FROM invoices`, which
            # would destroy whatever another test file has committed and not yet read back.
            session.execute(
                delete(Invoice).where(
                    Invoice.customer_id.in_(
                        select(Customer.id).where(Customer.ragione_sociale.like(f"{_PREFIX} %"))
                    )
                )
            )
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage))
            remove_azienda(session, azienda_id)
            session.commit()


def _dashboard(engine: Engine) -> CommercialDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_commercial_dashboard(PeriodoQuery(), READONLY)


def _read(engine: Engine, call: Callable[[DashboardService], Any]) -> Any:
    """One dashboard, on its own fresh session, with no barrier in the way."""
    with session_factory(engine)() as session:
        return call(DashboardService(session))


def _imminent_date() -> date:
    """A day inside the "prossime chiusure" window of the default (current month) period.

    The window runs from the period's last day for thirty days, so ten days past the end of
    the month is inside it and outside the period itself.
    """
    today = today_local()
    _, fine_periodo = month_bounds(today.year, today.month)
    _, imminente = window_from(fine_periodo, 10)
    return imminente


def _intruder_deal(seeded: Seeded) -> Deal:
    """The row 6B's version used: an open deal that also expects to close imminently.

    Shaped so that two independent queries would each report it -- it sits in an open stage
    (`pipeline_summary`) and closes inside the imminent window (`expected_closures`). One
    figure moving would be a coincidence; the pair is the property.
    """
    return Deal(
        nome=f"{_PREFIX} intruso",
        customer_id=seeded.customer_id,
        pipeline_stage_id=seeded.stages["lead"].id,
        valore_previsto=Decimal("9999.00"),
        probabilita=50,
        data_chiusura_prevista=_imminent_date(),
        custom_fields={},
    )


def _intruder_invoice(seeded: Seeded) -> Invoice:
    """An issued invoice, dated inside the default period and already past due.

    It is what a deal cannot be: a row the *economic* and *operational* dashboards would
    each report from more than one query. Issued today, so it falls in the current month
    (`fatture_emesse`, `pnl.in_corso.ricavi`); unpaid (`da_incassare`); due yesterday, so
    `_overdue_predicate`'s strict `<` catches it (`scaduto`, `scaduto_non_incassato`); and
    attached to the base deal, which sits on an `open` stage (`fatturato_non_vinto`) and
    gives `revenue_in_range` -- which groups by `deal_id` and skips nulls -- something to
    group by.
    """
    return Invoice(
        customer_id=seeded.customer_id,
        deal_id=seeded.deal_id,
        tipo="fattura",
        stato="emessa",
        stato_pagamento="da_incassare",
        imponibile=Decimal("9999.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("9999.00"),
        data_emissione=today_local(),
        data_scadenza=today_local() - timedelta(days=1),
        causale=f"{_PREFIX} intruso",
        tipo_documento="TD01",
        divisa="EUR",
        custom_fields={},
    )


_INTRUDERS: dict[str, Callable[[Seeded], Any]] = {
    "deal": _intruder_deal,
    "invoice": _intruder_invoice,
}


def _run_with_a_commit_in_the_middle(
    seeded: Seeded,
    monkeypatch: pytest.MonkeyPatch,
    *,
    call: Callable[[DashboardService], Any] = lambda service: service.get_commercial_dashboard(
        PeriodoQuery(), READONLY
    ),
    barrier_on: tuple[type, str] = (DealRepository, "pipeline_summary"),
    intruder: str = "deal",
) -> tuple[Any, datetime]:
    """Run one dashboard while another connection commits a row between `_open_snapshot()`
    and that dashboard's first aggregate.

    The barrier hangs off whichever aggregate the dashboard under test calls first --
    `pipeline_summary`, `period_pnl` or `week_hours` -- because that is what puts the commit
    after the snapshot was taken (`SELECT transaction_timestamp()` is the transaction's
    first statement and is what acquires it) and before every remaining query, which is
    exactly the window `READ COMMITTED` leaks through. Patching a method the dashboard never
    calls would leave the writer waiting on a barrier nobody trips, which is why the
    assertion below names the method rather than reporting a bare timeout.

    The intruder differs per dashboard for the same reason: a deal moves no figure on the
    economic page and an invoice moves none on the commercial one, so a single shared
    intruder would make two of the three runs pass without proving anything.

    Returns the response and an instant that provably precedes the parallel COMMIT, taken
    from the database's clock rather than the host's.
    """
    reader_reached_first_query = threading.Event()
    writer_committed = threading.Event()
    instant_before_commit: list[datetime] = []

    def writer() -> None:
        try:
            reader_reached_first_query.wait(_BARRIER_TIMEOUT)
            with session_factory(seeded.engine)() as session:
                session.add(_INTRUDERS[intruder](seeded))
                session.flush()
                instant_before_commit.append(
                    session.execute(text("SELECT clock_timestamp()")).scalar_one()
                )
                session.commit()
        finally:
            # In a `finally` so a writer that raises releases the reader immediately and
            # the assertion below reports the real failure, instead of the suite paying
            # `_BARRIER_TIMEOUT` seconds to reach the same conclusion.
            writer_committed.set()

    owner, method_name = barrier_on
    original = getattr(owner, method_name)
    tripped = threading.Event()

    def barrier(self: object, *args: object, **kwargs: object) -> object:
        if not tripped.is_set():
            tripped.set()
            reader_reached_first_query.set()
            writer_committed.wait(_BARRIER_TIMEOUT)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(owner, method_name, barrier)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        with session_factory(seeded.engine)() as session:
            result = call(DashboardService(session))
    finally:
        thread.join(timeout=_BARRIER_TIMEOUT)
    assert tripped.is_set(), (
        f"{owner.__name__}.{method_name} was never called, so nothing was synchronised; "
        "check that it is still the first aggregate this dashboard invokes"
    )
    assert instant_before_commit, "the parallel writer never committed"
    return result, instant_before_commit[0]


def _lead(result: CommercialDashboard) -> PipelineStageSummary:
    return next(row for row in result.pipeline if row.stage_code == "lead")


# -- clause (a): the constant is the level, and the lever works -------------------


@pytest.mark.parametrize(
    ("constant", "shown"),
    [("REPEATABLE READ", "repeatable read"), ("READ COMMITTED", "read committed")],
)
def test_the_constant_is_the_level_the_connection_actually_gets(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch, constant: str, shown: str
) -> None:
    """Both values, not just the shipped one.

    The shipped case is the criterion. The other case is what licenses the inversion test
    below to be read as evidence: if patching `SNAPSHOT_ISOLATION` did not actually reach
    the connection -- because the service had started reading a setting, or caching the
    execution option -- the inversion would fail for a reason that has nothing to do with
    the snapshot, and somebody would "fix" it by weakening clause (b).
    """
    assert dashboard_service.SNAPSHOT_ISOLATION == "REPEATABLE READ"
    monkeypatch.setattr(dashboard_service, "SNAPSHOT_ISOLATION", constant)
    with session_factory(seeded.engine)() as session:
        DashboardService(session).get_commercial_dashboard(PeriodoQuery(), READONLY)
        level = session.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == shown


# -- clause (b): a commit in the middle reaches no figure ------------------------


def test_clause_b_a_commit_before_the_first_aggregate_appears_in_no_figure(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The intruder commits before the *first* aggregate runs, and is still invisible.

    That is the sharper half of the claim: not "a later query agrees with an earlier one",
    but "the snapshot was already fixed by the time any aggregate ran". Two unrelated
    figures are checked, because a single one could be right for its own reasons.
    """
    result, _instant = _run_with_a_commit_in_the_middle(seeded, monkeypatch)

    lead = _lead(result)
    assert lead.numero == 1
    assert lead.valore_totale == Decimal("1000.00")
    assert lead.valore_ponderato == Decimal("500.00")
    assert result.chiusure_previste_30_giorni == 0


def test_the_intruder_really_was_committed_and_the_dashboard_can_see_it(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the test above, and the reason it is not vacuous.

    A writer that silently did nothing would satisfy every assertion of clause (b). So the
    same row is read back by a *fresh* dashboard, with the same predicates, and both
    figures must move. Clause (b) then means "invisible to that snapshot", not "absent".
    """
    _run_with_a_commit_in_the_middle(seeded, monkeypatch)

    later = _dashboard(seeded.engine)
    assert _lead(later).numero == 2
    assert _lead(later).valore_totale == Decimal("10999.00")
    assert later.chiusure_previste_30_giorni == 1


# -- clause (c): the instant is the snapshot's, not the response's ---------------


def test_clause_c_calcolato_alle_precedes_the_parallel_commit(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`calcolato_alle` is when the reading happened, not when the answering finished.

    The bound is taken with `clock_timestamp()` on the writer's connection immediately
    before its COMMIT, so it is a real lower bound on that commit rather than a reading
    taken after it. A `calcolato_alle` filled in at the end of the request -- with
    `clock_timestamp()`, or in Python -- lands after this instant and fails here, while
    still passing a bracket taken around the whole call.
    """
    result, instant_before_commit = _run_with_a_commit_in_the_middle(seeded, monkeypatch)
    assert result.calcolato_alle < instant_before_commit, (
        f"calcolato_alle {result.calcolato_alle} is not before the parallel commit at "
        f"{instant_before_commit}; it is not the snapshot's instant"
    )


# -- the inversion ---------------------------------------------------------------


def test_the_inversion_read_committed_leaks_the_parallel_commit(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test that makes clause (a) mean something.

    Forced to `READ COMMITTED` -- Postgres's default, and therefore what this dashboard
    would silently get if nobody had said otherwise -- each statement takes its own
    snapshot, so every query after the barrier sees the intruder. If this test ever starts
    passing with clause (b)'s numbers, the isolation level has stopped doing anything and
    clauses (a) to (c) are decorative.
    """
    monkeypatch.setattr(dashboard_service, "SNAPSHOT_ISOLATION", "READ COMMITTED")

    result, _instant = _run_with_a_commit_in_the_middle(seeded, monkeypatch)

    lead = _lead(result)
    assert lead.numero == 2, (
        "in READ COMMITTED the post-barrier queries should have seen the parallel commit. "
        "They did not, which means the barrier is not actually landing between two "
        "statements -- fix the barrier before trusting clause (b)."
    )
    assert lead.valore_totale == Decimal("10999.00")
    assert result.chiusure_previste_30_giorni == 1


# -- criterion 6 on every dashboard, not on the first one built -------------------


def _economic(service: DashboardService) -> EconomicDashboard:
    return service.get_economic_dashboard(PeriodoQuery(), READONLY)


def _operational(service: DashboardService) -> OperationalDashboard:
    return service.get_operational_dashboard(READONLY)


def _economic_figures(result: EconomicDashboard) -> dict[str, object]:
    """The four figures the invoice intruder would reach, from four separate queries.

    `fatture_emesse` and `ricavi_in_corso` come from two different owners --
    `InvoiceRepository` and `AnalyticsService` -- so agreement between them is not a
    property of one query having been run once.
    """
    return {
        "fatture_emesse": result.fatture_emesse,
        "da_incassare": result.da_incassare,
        "scaduto": result.scaduto,
        "ricavi_in_corso": result.pnl.in_corso.ricavi,
    }


def _operational_figures(result: OperationalDashboard) -> dict[str, object]:
    conteggi = {signal.codice: signal.conteggio for signal in result.segnali}
    return {
        "fatturato_non_vinto": conteggi["fatturato_non_vinto"],
        "scaduto_non_incassato": conteggi["scaduto_non_incassato"],
    }


class _Dashboard(NamedTuple):
    label: str
    call: Callable[[DashboardService], Any]
    barrier_on: tuple[type, str]
    intruder: str
    figures: Callable[[Any], dict[str, object]]


_ALL_THREE = [
    _Dashboard(
        "commerciale",
        lambda service: service.get_commercial_dashboard(PeriodoQuery(), READONLY),
        (DealRepository, "pipeline_summary"),
        "deal",
        lambda result: {
            "lead.numero": _lead(result).numero,
            "lead.valore_totale": _lead(result).valore_totale,
            "chiusure_previste_30_giorni": result.chiusure_previste_30_giorni,
        },
    ),
    _Dashboard(
        "economica",
        _economic,
        (AnalyticsService, "period_pnl"),
        "invoice",
        _economic_figures,
    ),
    _Dashboard(
        "operativa",
        _operational,
        (TimeEntryRepository, "week_hours"),
        "invoice",
        _operational_figures,
    ),
]

_IDS = [case.label for case in _ALL_THREE]


@pytest.mark.parametrize("case", _ALL_THREE, ids=_IDS)
def test_clause_a_every_dashboard_runs_in_repeatable_read(seeded: Seeded, case: _Dashboard) -> None:
    """Criterion 6 applies to *every* dashboard, not to the first one built.

    Asserted on the connection rather than on the constant, so a dashboard written without
    a call to `_open_snapshot` -- the one way a new page can skip this entirely -- reports
    `read committed` here instead of inheriting the guarantee by association.
    """
    with session_factory(seeded.engine)() as session:
        case.call(DashboardService(session))
        level = session.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == "repeatable read", case.label


@pytest.mark.parametrize("case", _ALL_THREE[1:], ids=_IDS[1:])
def test_clause_b_the_new_dashboards_see_no_mid_flight_commit(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch, case: _Dashboard
) -> None:
    """The criterion's own wording, on the two dashboards 6C adds: a parallel connection
    COMMITs between the snapshot and the first internal query, and that row appears in
    **no** figure of the response -- not in the money and not in the count.

    Both are asserted, and that is the point of the pair rather than of a total alone: an
    implementation that leaked the row into `fatture_emesse` while keeping the revenue
    right would pass a test that only checked the money, and that is exactly the shape a
    partially-snapshotted page takes.

    The expectation is a reading taken moments earlier on the same engine, not a literal
    zero. `da_incassare`, `scaduto` and the two signals are whole-register aggregates with
    no period and no name to scope them, so a row left committed by another test file would
    move a literal and turn a real failure into an unexplainable one.
    """
    baseline = case.figures(_read(seeded.engine, case.call))

    result, _instant = _run_with_a_commit_in_the_middle(
        seeded,
        monkeypatch,
        call=case.call,
        barrier_on=case.barrier_on,
        intruder=case.intruder,
    )

    assert case.figures(result) == baseline, case.label


@pytest.mark.parametrize("case", _ALL_THREE[1:], ids=_IDS[1:])
def test_the_intruder_moves_every_one_of_those_figures_afterwards(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch, case: _Dashboard
) -> None:
    """The control that stops the test above from being satisfied by a writer that did
    nothing -- and it is stricter than "something changed".

    **Every** figure clause (b) named must move. A figure that never moves is a figure
    clause (b) was never testing: it would have equalled the baseline whatever the
    isolation level, and leaving it in the comparison quietly weakens the whole assertion
    by one term.
    """
    baseline = case.figures(_read(seeded.engine, case.call))

    _run_with_a_commit_in_the_middle(
        seeded,
        monkeypatch,
        call=case.call,
        barrier_on=case.barrier_on,
        intruder=case.intruder,
    )

    after = case.figures(_read(seeded.engine, case.call))
    for name, before in baseline.items():
        assert after[name] != before, (
            f"{case.label}: {name} did not move when the intruder was committed, so "
            "clause (b) is not testing it"
        )


@pytest.mark.parametrize("case", _ALL_THREE[1:], ids=_IDS[1:])
def test_the_inversion_read_committed_leaks_into_the_new_dashboards_too(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch, case: _Dashboard
) -> None:
    """What licenses clause (b) on each new page: forced to `READ COMMITTED`, the same run
    leaks the intruder into every one of those figures.

    Per dashboard rather than once, because the barrier lands on a different method in each
    -- and a barrier that fired *before* the snapshot, or after the last query, would give
    clause (b) its green for a reason that has nothing to do with the isolation level. If
    this ever starts agreeing with clause (b)'s baseline, the barrier has stopped landing
    between two statements and clause (b) is decorative.
    """
    baseline = case.figures(_read(seeded.engine, case.call))
    monkeypatch.setattr(dashboard_service, "SNAPSHOT_ISOLATION", "READ COMMITTED")

    result, _instant = _run_with_a_commit_in_the_middle(
        seeded,
        monkeypatch,
        call=case.call,
        barrier_on=case.barrier_on,
        intruder=case.intruder,
    )

    leaked = case.figures(result)
    for name, before in baseline.items():
        assert leaked[name] != before, (
            f"{case.label}: in READ COMMITTED the post-barrier queries should have seen "
            f"the parallel commit, and {name} did not move. The barrier is not landing "
            "between two statements -- fix it before trusting clause (b)."
        )
