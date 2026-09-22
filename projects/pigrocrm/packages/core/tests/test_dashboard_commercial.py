"""§4's dashboard, composed and nothing more.

Every figure here is checked against the repository that produced it, not recomputed:
recomputing in the test would make the test the second source of truth §1 forbids, and a
test that agrees with a wrong implementation is worse than no test.

"One endpoint, one transaction, one instant" is three claims, and the third is the one that
is normally faked by observing that two figures happen to agree.
`test_every_figure_comes_from_one_instant` commits a deal from a second connection
*between* two of the dashboard's own queries and requires the second query not to see it:
under `READ COMMITTED` -- what Postgres gives anyone who says nothing -- it does, and the
card disagrees with its own drill-through. That is the property, and nothing weaker
demonstrates it.

This file uses its own committed sessions because the service needs a transaction it can
set the isolation level on, and `db_session` holds an outer one open. Everything it commits
is removed in the teardown, `pipeline_stages` included: `db_engine` is session-scoped, and
six stages left behind would be six stages every other test in the suite did not create.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, func, select, text

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import (
    MAX_PERIOD_DAYS,
    CommercialDashboard,
    PeriodoQuery,
)
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds, session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService

READONLY = Actor(id=uuid7(), type="user", role="readonly")
SEED = Actor(id=None, type="system", role="admin")


class Seeded(NamedTuple):
    engine: Engine
    customer_id: UUID
    stages: dict[str, PipelineStageRead]


@pytest.fixture
def seeded(db_engine: Engine) -> Iterator[Seeded]:
    """Committed rows on their own session, cleaned up afterwards.

    `db_session` cannot be used: it holds an outer transaction open, and Postgres refuses
    `SET TRANSACTION ISOLATION LEVEL` once a transaction has begun -- which is exactly
    what `DashboardService` does first.

    The teardown empties `pipeline_stages` wholesale, following
    `test_automation_atomicity.py`: this file and that one are the only places in the suite
    that commit stages, and leftovers would make every test that lists the pipeline see six
    stages it did not create.
    """
    factory = session_factory(db_engine)
    with factory() as session:
        PipelineService(session).seed_defaults(SEED)
        stages = {s.code: s for s in PipelineService(session).list() if s.code is not None}
        customer = Customer(ragione_sociale="DASH Cliente", nazione="IT", custom_fields={})
        session.add(customer)
        session.flush()
        customer_id = customer.id

        session.add(
            Deal(
                nome="DASH aperto",
                customer_id=customer_id,
                pipeline_stage_id=stages["lead"].id,
                valore_previsto=Decimal("1000.00"),
                probabilita=50,
                data_chiusura_prevista=today_local(),
                custom_fields={},
            )
        )
        session.add(
            Deal(
                nome="DASH vinto",
                customer_id=customer_id,
                pipeline_stage_id=stages["vinto"].id,
                valore_previsto=Decimal("5000.00"),
                probabilita=100,
                chiuso_il=today_local(),
                custom_fields={},
            )
        )
        session.add(
            Deal(
                nome="DASH perso",
                customer_id=customer_id,
                pipeline_stage_id=stages["perso"].id,
                valore_previsto=Decimal("2000.00"),
                probabilita=0,
                chiuso_il=today_local(),
                custom_fields={},
            )
        )
        session.flush()
        session.add(
            Document(
                customer_id=customer_id,
                tipo="offerta",
                titolo="DASH offerta",
                stato="inviata",
                stato_dal=date(2026, 1, 1),
                versione_corrente=1,
                custom_fields={},
            )
        )
        session.commit()
    try:
        yield Seeded(db_engine, customer_id, stages)
    finally:
        with factory() as session:
            session.execute(delete(Document).where(Document.titolo.like("DASH %")))
            session.execute(delete(Deal).where(Deal.nome.like("DASH %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like("DASH %")))
            session.execute(delete(PipelineStage))
            session.commit()


def _dashboard(
    engine: Engine, da: date | None = None, a: date | None = None
) -> CommercialDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_commercial_dashboard(
            PeriodoQuery(da=da, a=a), READONLY
        )


def _this_month() -> tuple[date, date]:
    today = today_local()
    return month_bounds(today.year, today.month)


# -- the period ------------------------------------------------------------------


def test_the_period_defaults_to_the_current_month(seeded: Seeded) -> None:
    result = _dashboard(seeded.engine)
    assert (result.periodo.da, result.periodo.a) == _this_month()


def test_the_period_is_echoed_back_normalised(seeded: Seeded) -> None:
    """Always echoed, because a screenshot of a dashboard with no explicit period is a
    number with no unit (§4)."""
    result = _dashboard(seeded.engine, date(2026, 3, 1), date(2026, 3, 31))
    assert result.periodo.da == date(2026, 3, 1)
    assert result.periodo.a == date(2026, 3, 31)


def test_an_inverted_period_is_a_named_validation_error(seeded: Seeded) -> None:
    with pytest.raises(ValidationFailed) as caught:
        _dashboard(seeded.engine, date(2026, 3, 31), date(2026, 3, 1))
    assert caught.value.details["field"] == "da"


def test_an_absurdly_long_period_is_refused(seeded: Seeded) -> None:
    """§7.3: the predicate always carries a bounded period. Without a ceiling,
    `da=0001-01-01` is a full scan requested from a query string."""
    with pytest.raises(ValidationFailed) as caught:
        _dashboard(seeded.engine, date(1900, 1, 1), date(2026, 12, 31))
    assert caught.value.details["field"] == "a"


def test_the_ceiling_is_at_max_period_days_exactly(seeded: Seeded) -> None:
    """The boundary, because a limit tested only from a thousand years away is a limit
    whose off-by-one nobody has looked at."""
    da = date(2020, 1, 1)
    accepted = _dashboard(seeded.engine, da, da + timedelta(days=MAX_PERIOD_DAYS))
    assert accepted.periodo.da == da

    with pytest.raises(ValidationFailed) as caught:
        _dashboard(seeded.engine, da, da + timedelta(days=MAX_PERIOD_DAYS + 1))
    assert caught.value.details["field"] == "a"


@pytest.mark.parametrize(
    ("da", "a"),
    [(date(2026, 3, 1), None), (None, date(2026, 3, 31))],
)
def test_supplying_only_one_bound_is_refused(
    seeded: Seeded, da: date | None, a: date | None
) -> None:
    """Half a period is not a period, and guessing the other half would silently answer a
    different question from the one asked. Both halves are checked: a rule written as
    `if self.da is None` alone accepts the other spelling."""
    with pytest.raises(ValidationFailed) as caught:
        _dashboard(seeded.engine, da, a)
    assert caught.value.details["field"] == ("da" if da is None else "a")


# -- one instant -----------------------------------------------------------------


def _db_now(engine: Engine) -> datetime:
    with session_factory(engine)() as session:
        return session.execute(text("SELECT clock_timestamp()")).scalar_one()


def test_calcolato_alle_is_the_transaction_timestamp(seeded: Seeded) -> None:
    """Bracketed by the *database's* clock, not the host's.

    The container's clock and the host's drift by milliseconds -- enough to make a bracket
    taken with `datetime.now(UTC)` fail once in a while for a reason that has nothing to do
    with the dashboard. Taking both bounds from the same clock that produced the value
    keeps the assertion sharp: a hard-coded instant, a stale one, or one computed in Python
    on a host whose clock disagrees with the database all still fail it.
    """
    before = _db_now(seeded.engine)
    result = _dashboard(seeded.engine)
    after = _db_now(seeded.engine)
    assert before <= result.calcolato_alle <= after
    assert result.calcolato_alle.tzinfo is not None


def test_the_service_runs_in_repeatable_read(seeded: Seeded) -> None:
    """The level, read from the connection the service actually used. The test below
    proves the level does something; this proves it was set."""
    with session_factory(seeded.engine)() as session:
        DashboardService(session).get_commercial_dashboard(PeriodoQuery(), READONLY)
        level = session.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == "repeatable read"


def test_every_figure_comes_from_one_instant(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third of the three claims, and the one that is usually only asserted.

    A second connection commits a won deal *between* the dashboard's pipeline query and its
    closures query. In `REPEATABLE READ` the snapshot was taken before either, so the
    closures query cannot see it and the response describes one instant. In
    `READ COMMITTED` each statement takes its own snapshot, the closure count includes a
    deal the pipeline card never saw, and a card disagrees with its own drill-through --
    criterion 2's exact failure.

    Two figures that happen to agree would prove none of this: they agree under both
    isolation levels whenever nothing is writing.
    """
    original = DealRepository.closed_in_period

    def intruding(self: DealRepository, da: date, a: date) -> object:
        with session_factory(seeded.engine)() as outside:
            outside.add(
                Deal(
                    nome="DASH intruso",
                    customer_id=seeded.customer_id,
                    pipeline_stage_id=seeded.stages["vinto"].id,
                    valore_previsto=Decimal("7000.00"),
                    probabilita=100,
                    chiuso_il=today_local(),
                    custom_fields={},
                )
            )
            outside.commit()
        return original(self, da, a)

    monkeypatch.setattr(DealRepository, "closed_in_period", intruding)
    da, a = _this_month()
    result = _dashboard(seeded.engine, da, a)

    assert result.chiusure.vinti == 1, "the dashboard saw a deal committed after its snapshot"
    assert result.chiusure.valore_vinto == Decimal("5000.00")


def test_a_session_already_in_a_transaction_fails_loudly(db_engine: Engine) -> None:
    """The failure mode that must never be silent.

    If the isolation level cannot be set, the dashboard runs in READ COMMITTED and returns
    a total that was true at no single instant -- and nothing about re-reading the service
    would reveal it. So it raises instead.
    """
    with session_factory(db_engine)() as session:
        session.execute(text("SELECT 1"))  # opens a transaction
        with pytest.raises(RuntimeError, match="REPEATABLE READ"):
            DashboardService(session).get_commercial_dashboard(PeriodoQuery(), READONLY)


# -- composition, not computation ------------------------------------------------


def test_the_pipeline_matches_the_repository_verbatim(seeded: Seeded) -> None:
    """Composition, not computation: the service returns what the repository produced,
    with the same names and the same values (§3 form 1)."""
    result = _dashboard(seeded.engine)
    with session_factory(seeded.engine)() as session:
        expected = DealRepository(session).pipeline_summary()
    assert result.pipeline == expected
    assert next(r for r in result.pipeline if r.stage_code == "lead").numero == 1


def test_the_pipeline_carries_every_stage_and_says_which_kind_it_is(seeded: Seeded) -> None:
    """The card draws the whole pipeline, not the open half of it.

    A stage list that stops at the last open stage tells the reader nothing about where
    the work ended up, and «Vinto» and «Perso» are the two stages a pipeline exists to
    reach. They come back ordered by `posizione` like every other stage, and each row
    declares its `stage_tipo` so the renderer can group them without matching `nome` --
    a string the user is free to rename (residuo R15).
    """
    result = _dashboard(seeded.engine)
    by_code = {row.stage_code: row for row in result.pipeline}
    assert set(by_code) == {"lead", "contattato", "offerta", "negoziazione", "vinto", "perso"}
    assert [row.posizione for row in result.pipeline] == sorted(
        row.posizione for row in result.pipeline
    )
    assert by_code["lead"].stage_tipo == "open"
    assert by_code["vinto"].stage_tipo == "won"
    assert by_code["perso"].stage_tipo == "lost"
    assert by_code["vinto"].numero == 1
    assert by_code["perso"].numero == 1
    assert by_code["vinto"].valore_totale == Decimal("5000.00")


def test_the_closed_stages_count_what_sits_there_today_whatever_the_period(
    seeded: Seeded,
) -> None:
    """`Vinto` and `Perso` here are a *place*, not a period.

    The two closure figures above the card already answer "what closed between these two
    dates"; the pipeline answers "where are the deals now". Filtering these rows by the
    period would give the same card two meanings and make the counts disagree with the
    drill-through, which is not period-filtered either.
    """
    seeded_deals = _dashboard(seeded.engine, date(2019, 1, 1), date(2019, 12, 31))
    by_code = {row.stage_code: row for row in seeded_deals.pipeline}
    assert by_code["vinto"].numero == 1
    assert by_code["perso"].numero == 1
    assert seeded_deals.chiusure.vinti == 0
    assert seeded_deals.chiusure.persi == 0


def test_the_closures_match_the_repository_verbatim(seeded: Seeded) -> None:
    da, a = _this_month()
    result = _dashboard(seeded.engine, da, a)
    with session_factory(seeded.engine)() as session:
        expected = DealRepository(session).closed_in_period(da, a)
    assert result.chiusure == expected
    assert result.chiusure.vinti == 1
    assert result.chiusure.persi == 1
    assert result.chiusure.tasso_conversione == Decimal("50.00")


def test_the_pending_offers_carry_their_age(seeded: Seeded) -> None:
    result = _dashboard(seeded.engine)
    offer = next(o for o in result.offerte_in_attesa if o.titolo == "DASH offerta")
    assert offer.giorni == (today_local() - date(2026, 1, 1)).days
    assert result.offerte_in_attesa_totale >= 1


def test_the_expected_closures_window_runs_thirty_days_past_the_period(
    seeded: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The card says "prossimi 30 giorni", and the window it means starts where the period
    ends -- not at the period's own start, which would make a January dashboard report
    February's closures as imminent.

    Three deals: one inside the period itself, one twenty days after it ends, one fifty.
    Only the middle one is imminent -- an implementation that starts the window at the
    period's start counts the first as well, and one that widens it counts the last.
    """
    da, a = _this_month()
    with session_factory(seeded.engine)() as session:
        # The seeded open deal expects to close *today*, which is inside the period and
        # outside the window; clearing it keeps this assertion independent of which day of
        # the month the suite happens to run on.
        session.execute(
            text("UPDATE deals SET data_chiusura_prevista = NULL WHERE nome = 'DASH aperto'")
        )
        for nome, quando in (
            ("DASH dentro", da + timedelta(days=1)),
            ("DASH vicino", a + timedelta(days=20)),
            ("DASH lontano", a + timedelta(days=50)),
        ):
            session.add(
                Deal(
                    nome=nome,
                    customer_id=seeded.customer_id,
                    pipeline_stage_id=seeded.stages["contattato"].id,
                    valore_previsto=Decimal("100.00"),
                    probabilita=50,
                    data_chiusura_prevista=quando,
                    custom_fields={},
                )
            )
        session.commit()

    result = _dashboard(seeded.engine, da, a)
    assert result.chiusure_previste_30_giorni == 1


def test_the_unattributable_count_is_declared(seeded: Seeded) -> None:
    """§4.1. Zero is a real answer here and the field still has to be present: the number
    exists so the reader is told what the conversion rate was computed on."""
    with session_factory(seeded.engine)() as session:
        session.add(
            Deal(
                nome="DASH storico",
                customer_id=seeded.customer_id,
                pipeline_stage_id=seeded.stages["vinto"].id,
                valore_previsto=Decimal("900.00"),
                probabilita=100,
                chiuso_il=None,
                custom_fields={},
            )
        )
        session.commit()

    assert _dashboard(seeded.engine).chiusure_non_attribuibili == 1


def test_the_signal_is_present_on_the_commercial_dashboard(seeded: Seeded) -> None:
    """§6.2 and §17: this signal ships with the automation it cross-checks, on the
    dashboard that needs no invoices."""
    result = _dashboard(seeded.engine)
    assert result.offerte_accettate_deal_non_vinto == 0

    with session_factory(seeded.engine)() as session:
        deal = session.execute(text("SELECT id FROM deals WHERE nome = 'DASH aperto'")).scalar_one()
        session.add(
            Document(
                deal_id=deal,
                tipo="offerta",
                titolo="DASH accettata",
                stato="accettata",
                stato_dal=date(2026, 1, 1),
                versione_corrente=1,
                custom_fields={},
            )
        )
        session.commit()

    assert _dashboard(seeded.engine).offerte_accettate_deal_non_vinto == 1


def test_a_readonly_actor_sees_the_whole_dashboard(seeded: Seeded) -> None:
    """§13: no new role and no new authorisation rule. Every dashboard is visible to
    whoever can read the services it reads, and slice 4 §11 gives those to every role. The
    only admin-only figure in that area is the fiscal estimate, which is on no dashboard
    (§5.3)."""
    result = _dashboard(seeded.engine)
    assert result.pipeline
    assert result.offerte_in_attesa


def test_the_service_has_exactly_the_dashboards_that_exist() -> None:
    """One entry per dashboard, and each of them with its own MCP tool. Pinned here so a
    method added without a tool fails in this file rather than in the architecture test,
    where the message is about a list.

    6B shipped one; Tasks C4 and C6 added the other two and registered their tools in the
    same commits, for the reason `tools/__init__.py` records beside each. The set is
    widened by the task that adds the method and never ahead of it -- a set widened early
    leaves the assertion green over a method that does not exist, which is a pin that has
    stopped pinning.
    """
    import inspect

    public = {
        name
        for name, member in inspect.getmembers(DashboardService, predicate=inspect.isfunction)
        if not name.startswith("_") and member.__qualname__.startswith("DashboardService.")
    }
    assert public == {
        "get_commercial_dashboard",
        "get_economic_dashboard",
        "get_operational_dashboard",
        # Slice 8 part A (REB-329), registered as `get_receivables_dashboard` in the
        # same commit.
        "get_receivables_dashboard",
    }


def test_the_service_never_writes(seeded: Seeded) -> None:
    """§7.2: no dashboard writes anything, so there is no state to repair and no
    "recompute" button that does anything but re-read. A read-only transaction is also what
    makes `REPEATABLE READ` free: a serialisation failure can only strike a writer."""
    with session_factory(seeded.engine)() as session:
        DashboardService(session).get_commercial_dashboard(PeriodoQuery(), READONLY)
        assert not session.new and not session.dirty and not session.deleted


def _counts(engine: Engine) -> tuple[int, int, int]:
    with session_factory(engine)() as session:
        return (
            session.execute(select(func.count(Deal.id))).scalar_one(),
            session.execute(select(func.count(Document.id))).scalar_one(),
            session.execute(select(func.count(Activity.id))).scalar_one(),
        )


def test_the_dashboard_leaves_the_database_exactly_as_it_found_it(seeded: Seeded) -> None:
    """The same claim from outside the session, because "nothing was flushed" and "nothing
    was written" are not the same statement. `activities` is counted too: a dashboard that
    recorded its own reads would be a write nobody asked for."""
    before = _counts(seeded.engine)
    _dashboard(seeded.engine)
    assert _counts(seeded.engine) == before
