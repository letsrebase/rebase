"""**Criterion 1**, in both directions. §5's economic dashboard.

A figure on this page has to reconcile *down* -- the dashboard agrees with the service
that owns the quantity -- and *up* -- that service agrees with the rows somebody actually
wrote. A test that recomputes its expectation with the same query the code uses proves
only that the code is deterministic, so every figure here is pinned to a literal derived
by hand from the fixture's own rows, and cross-read once through a raw `SELECT` that
touches no service at all.

The fixture writes an **RF01** shape, where `imponibile` and `totale` diverge: `1000.00`
net at 22% is `1220.00` gross. Under the forfettario profile the suite ships the two
columns are equal, so a corpus built on the default cannot tell a correct implementation
from one that reads the wrong column -- revenue must follow `imponibile` and the
receivable must follow `totale`, and each assertion below states the wrong value it must
*not* equal so the mutation is named rather than merely excluded.

The invoices are attached to deals on purpose. `AnalyticsRepository.revenue_in_range`
groups by `deal_id` and skips `deal_id IS NULL` entirely, so a corpus of unlinked
invoices would report zero revenue and every reconciliation here would pass against an
implementation that read nothing at all. Two deals, one on an `open` stage and one on a
`won` stage with no unbilled hours, also put a known figure in *each* of §7.4's two
columns rather than only in their sum.

This file uses committed sessions of its own, like `test_dashboard_commercial.py`:
`DashboardService` sets the isolation level as its first statement and Postgres refuses
that once a transaction has begun, so `db_session` -- which holds an outer one open --
cannot be used.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from fakes.azienda_fixtures import committed_default_azienda, remove_azienda
from sqlalchemy import Engine, Select, delete, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import EconomicDashboard, PeriodoQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds, session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage

READONLY = Actor(id=uuid7(), type="user", role="readonly")

_PREFIX = "ECON"
_DA = date(2026, 3, 1)
_A = date(2026, 3, 31)

# Every figure below, derived by hand from the four rows the fixture writes. They are
# literals rather than expressions for the reason criterion 1 exists: an expectation
# recomputed the way the code computes it agrees with a wrong implementation.
_RICAVI_IN_CORSO = Decimal("1000.00")  # the March invoice on the open deal, imponibile
_RICAVI_CHIUSI = Decimal("500.00")  # the March invoice on the won deal, imponibile
_RICAVI_PERIODO = Decimal("1500.00")  # their sum, which is what the raw SELECT returns
_RICAVI_SE_SEGUISSE_TOTALE = Decimal("1830.00")  # 1220.00 + 610.00
_DA_INCASSARE = Decimal("14028.78")  # 1220.00 + 610.00 + 12198.78, no period
_DA_INCASSARE_SE_SEGUISSE_IMPONIBILE = Decimal("11499.00")  # 1000 + 500 + 9999
_SCADUTO = Decimal("1220.00")  # the one row whose data_scadenza is already past
_FATTURE_EMESSE = 2


class Corpus(NamedTuple):
    engine: Engine
    aperto_id: UUID
    vinto_id: UUID


@pytest.fixture
def rf01_corpus(db_engine: Engine) -> Iterator[Corpus]:
    """Four invoices whose `imponibile` and `totale` differ, committed.

    Two deals, because the P&L splits its rows into `chiusi` and `in_corso` by the state
    of the deal each invoice belongs to (`TimeEntryService.deal_summary`): a deal on an
    `open` stage is "in corso", a deal on a `won` stage with no unbilled billable hours is
    "chiuso". One known figure in each column is what makes the split assertable rather
    than only their total.

    The stages carry the `_PREFIX` in their names and are deleted individually in the
    teardown -- not with a wholesale `DELETE FROM pipeline_stages`, which is what
    `test_dashboard_commercial.py` does and can only do because it is the file that seeds
    the defaults.
    """
    factory = session_factory(db_engine)
    with factory() as session:
        _require_an_empty_register(session)
        azienda_id = committed_default_azienda(session)
        aperto = PipelineStage(
            nome=f"{_PREFIX} aperto", posizione=0, probabilita_default=20, tipo="open"
        )
        vinto = PipelineStage(
            nome=f"{_PREFIX} vinto", posizione=1, probabilita_default=100, tipo="won"
        )
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        session.add_all([aperto, vinto, customer])
        session.flush()
        deal_aperto = Deal(
            nome=f"{_PREFIX} in corso",
            customer_id=customer.id,
            pipeline_stage_id=aperto.id,
            probabilita=20,
            custom_fields={},
        )
        deal_vinto = Deal(
            nome=f"{_PREFIX} chiuso",
            customer_id=customer.id,
            pipeline_stage_id=vinto.id,
            probabilita=100,
            chiuso_il=date(2026, 3, 25),
            custom_fields={},
        )
        session.add_all([deal_aperto, deal_vinto])
        session.flush()
        # In the period, on the deal still in progress, and already past its due date:
        # this row alone is `scaduto`, which is what makes that figure distinguishable
        # from `da_incassare` instead of trivially equal to it.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_aperto.id,
            imponibile="1000.00",
            totale="1220.00",
            data_emissione=date(2026, 3, 10),
            data_scadenza=date(2026, 4, 10),
        )
        # In the period, on the won deal, not yet due.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_vinto.id,
            imponibile="500.00",
            totale="610.00",
            data_emissione=date(2026, 3, 20),
            data_scadenza=None,
        )
        # Outside the period and unpaid: in `da_incassare`, in neither revenue column and
        # not in `fatture_emesse`. The asymmetry §5.2 is about.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_aperto.id,
            imponibile="9999.00",
            totale="12198.78",
            data_emissione=date(2026, 4, 1),
            data_scadenza=None,
        )
        # In the period and annulled: in no figure at all. A row that only a filter can
        # exclude, and the register keeps it.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_vinto.id,
            stato="annullata",
            imponibile="7777.00",
            totale="9487.94",
            data_emissione=date(2026, 3, 15),
            data_scadenza=date(2026, 4, 15),
        )
        session.commit()
        ids = Corpus(db_engine, deal_aperto.id, deal_vinto.id)
    try:
        yield ids
    finally:
        with factory() as session:
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(_corpus_customers())))
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            remove_azienda(session, azienda_id)
            session.commit()


def _corpus_customers() -> Select[tuple[UUID]]:
    return select(Customer.id).where(Customer.ragione_sociale.like(f"{_PREFIX} %"))


def _require_an_empty_register(session: Session) -> None:
    """`da_incassare` and `scaduto` have **no period and no scope**: they are sums over the
    whole register. So the literals above are only true if this file's rows are the only
    committed invoices in the database, and that is a precondition worth failing loudly on
    rather than an off-by-N nobody can read. Every other suite in `packages/core/tests`
    writes its invoices on `db_session`, which is rolled back.
    """
    leftovers = session.execute(select(func.count(Invoice.id))).scalar_one()
    assert leftovers == 0, (
        f"{leftovers} committed invoice(s) were already in the register. The whole-register "
        "figures on this dashboard have no period and no scope, so another suite leaving "
        "invoices behind changes this file's expected values."
    )


def _add_invoice(
    session: Session,
    *,
    customer_id: UUID,
    deal_id: UUID,
    imponibile: str,
    totale: str,
    data_emissione: date,
    data_scadenza: date | None,
    stato: str = "emessa",
) -> None:
    """Written directly, not through `InvoiceService`: that path recomputes `imponibile`
    and `totale` from the lines under the forfettario profile the suite ships, which makes
    them equal -- exactly the case these tests must be able to step outside of.

    `anno`/`numero` stay null: `ck_invoices_anno_numero_together` requires them to move as
    a pair, and no figure on this page reads either.
    """
    session.add(
        Invoice(
            customer_id=customer_id,
            deal_id=deal_id,
            tipo="fattura",
            stato=stato,
            stato_pagamento="da_incassare",
            imponibile=Decimal(imponibile),
            imposta=Decimal("0.00"),
            bollo=Decimal("0.00"),
            totale=Decimal(totale),
            data_emissione=data_emissione,
            data_scadenza=data_scadenza,
            tipo_documento="TD01",
            divisa="EUR",
            custom_fields={},
        )
    )


def _dashboard(engine: Engine, da: date | None = _DA, a: date | None = _A) -> EconomicDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_economic_dashboard(PeriodoQuery(da=da, a=a), READONLY)


# --- criterion 1 downward: the dashboard against the service that owns the figure ----


def test_the_revenue_equals_the_owning_service_to_the_cent(rf01_corpus: Corpus) -> None:
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        pnl = AnalyticsService(session).period_pnl(PeriodPnlQuery(da=_DA, a=_A), READONLY)
    assert result.pnl.chiusi.ricavi == pnl.chiusi.ricavi
    assert result.pnl.in_corso.ricavi == pnl.in_corso.ricavi
    assert isinstance(result.pnl.chiusi.ricavi, Decimal)


def test_the_pnl_is_embedded_verbatim_and_not_flattened(rf01_corpus: Corpus) -> None:
    """§5: there is no new aggregate on this page. Embedding the owning service's own model
    makes the reconciliation an identity rather than a comparison, and leaves no place for
    a field to be renamed or recombined on the way through."""
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        pnl = AnalyticsService(session).period_pnl(PeriodPnlQuery(da=_DA, a=_A), READONLY)
    assert result.pnl == pnl


def test_the_period_reaches_the_owning_service_unchanged(rf01_corpus: Corpus) -> None:
    """The composition's one real opportunity to lie: `period_pnl` echoes back the window
    it was given, so a dashboard that resolved one period and queried another is visible
    here and nowhere else. `customer_id` is `None` because §5 has no customer filter -- the
    dashboard is the whole business."""
    result = _dashboard(rf01_corpus.engine)
    assert (result.pnl.da, result.pnl.a) == (_DA, _A)
    assert result.periodo.da == _DA
    assert result.periodo.a == _A
    assert result.pnl.customer_id is None


# --- criterion 1 upward: the service against the rows somebody wrote -----------------


def test_the_revenue_equals_a_direct_sql_sum_on_an_independent_path(
    rf01_corpus: Corpus,
) -> None:
    """A path that touches no service, no repository and no ORM filter. If the dashboard
    and the P&L were both wrong in the same way, the comparison above would still agree --
    this one would not.

    Adding the two columns *inside a test* is not the total §7.4 forbids showing: the claim
    being checked is that `chiusi` and `in_corso` partition the same rows the raw `SELECT`
    sees, which is the property that makes the split honest rather than a place for a row
    to fall between two cards.
    """
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        direct = session.execute(
            text(
                "SELECT COALESCE(SUM(imponibile), 0) FROM invoices "
                "WHERE tipo = 'fattura' AND stato = 'emessa' AND deleted_at IS NULL "
                "AND data_emissione BETWEEN :da AND :a"
            ),
            {"da": _DA, "a": _A},
        ).scalar_one()
    assert Decimal(direct) == _RICAVI_PERIODO
    assert result.pnl.chiusi.ricavi + result.pnl.in_corso.ricavi == Decimal(direct)


def test_each_column_carries_its_own_known_figure(rf01_corpus: Corpus) -> None:
    """Not only the sum. A dashboard whose two columns were swapped, or which put every
    invoice in one of them, reconciles perfectly against a single total."""
    result = _dashboard(rf01_corpus.engine)
    assert result.pnl.chiusi.ricavi == _RICAVI_CHIUSI
    assert result.pnl.in_corso.ricavi == _RICAVI_IN_CORSO


def test_the_revenue_follows_imponibile_and_fails_if_it_follows_totale(
    rf01_corpus: Corpus,
) -> None:
    """§5.1: `fatturato = Σ imponibile`, and this slice introduces no third meaning."""
    result = _dashboard(rf01_corpus.engine)
    total = result.pnl.chiusi.ricavi + result.pnl.in_corso.ricavi
    assert total == _RICAVI_PERIODO
    assert total != _RICAVI_SE_SEGUISSE_TOTALE, "revenue must follow `imponibile`, not `totale`"


def test_da_incassare_follows_totale_and_fails_if_it_follows_imponibile(
    rf01_corpus: Corpus,
) -> None:
    """§5.2, specularly. The two quantities are not interchangeable: a receivable is what
    must arrive in the bank, VAT included -- money collected on the State's behalf."""
    result = _dashboard(rf01_corpus.engine)
    assert result.da_incassare == _DA_INCASSARE
    assert result.da_incassare != _DA_INCASSARE_SE_SEGUISSE_IMPONIBILE, (
        "da_incassare must follow `totale`, not `imponibile`"
    )


def test_scaduto_is_a_strict_subset_of_da_incassare(rf01_corpus: Corpus) -> None:
    """A subset, and neither equal to it nor zero: `<=` alone passes against a `scaduto`
    wired to the same aggregate as `da_incassare` *and* against one wired to nothing."""
    result = _dashboard(rf01_corpus.engine)
    assert result.scaduto == _SCADUTO
    assert result.scaduto < result.da_incassare


def test_the_receivable_has_no_period_and_the_revenue_does(rf01_corpus: Corpus) -> None:
    """The asymmetry, made explicit: an invoice issued in April and unpaid is still owed
    when the period asked about is March."""
    result = _dashboard(rf01_corpus.engine)
    assert result.fatture_emesse == _FATTURE_EMESSE
    assert result.da_incassare > (result.pnl.chiusi.ricavi + result.pnl.in_corso.ricavi)


def test_a_period_with_no_invoices_still_reports_the_whole_receivable(
    rf01_corpus: Corpus,
) -> None:
    """The sharpest form of the same asymmetry, and the one that catches a `da_incassare`
    that was quietly given the period: on a window where nothing was issued, the revenue
    and the count go to zero and the receivable does not move."""
    result = _dashboard(rf01_corpus.engine, date(2026, 1, 1), date(2026, 1, 31))
    assert result.fatture_emesse == 0
    assert result.pnl.chiusi.ricavi + result.pnl.in_corso.ricavi == Decimal("0.00")
    assert result.da_incassare == _DA_INCASSARE
    assert result.scaduto == _SCADUTO


def test_an_annulled_invoice_is_in_no_figure(rf01_corpus: Corpus) -> None:
    """The corpus carries an annulled March invoice of 7777.00/9487.94, already past due.
    It is in the register and in none of the four figures -- the struck-through page."""
    result = _dashboard(rf01_corpus.engine)
    assert result.pnl.chiusi.ricavi + result.pnl.in_corso.ricavi == _RICAVI_PERIODO
    assert result.fatture_emesse == _FATTURE_EMESSE
    assert result.da_incassare == _DA_INCASSARE
    assert result.scaduto == _SCADUTO


# --- the shape of the page ------------------------------------------------------------


def test_the_period_defaults_to_the_current_month(rf01_corpus: Corpus) -> None:
    result = _dashboard(rf01_corpus.engine, None, None)
    today = today_local()
    assert (result.periodo.da, result.periodo.a) == month_bounds(today.year, today.month)
    assert (result.pnl.da, result.pnl.a) == (result.periodo.da, result.periodo.a)


def test_calcolato_alle_is_the_instant_of_one_transaction(rf01_corpus: Corpus) -> None:
    """Bracketed by the database's own clock, as `test_dashboard_commercial.py` does: the
    container's clock and the host's drift by milliseconds."""
    with session_factory(rf01_corpus.engine)() as session:
        before = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        after = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    assert before <= result.calcolato_alle <= after
    assert result.calcolato_alle.tzinfo is not None


def test_the_service_runs_in_repeatable_read(rf01_corpus: Corpus) -> None:
    """One endpoint, one transaction, one instant. Proved for the commercial dashboard by
    a second connection committing mid-flight; asserted here because this page adds a
    second *service* to the same transaction and the level must survive that."""
    with session_factory(rf01_corpus.engine)() as session:
        DashboardService(session).get_economic_dashboard(PeriodoQuery(da=_DA, a=_A), READONLY)
        level = session.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == "repeatable read"


def test_the_margin_is_reported_in_two_columns_with_no_sum_of_them(
    rf01_corpus: Corpus,
) -> None:
    """Slice 4 §7.4: closed and in-progress, and the reportable figure is the first. There
    is deliberately no field holding their sum -- adding a finished job's margin to a
    half-done one produces a figure that is neither, and that moves every week for reasons
    which are not business performance."""
    result = _dashboard(rf01_corpus.engine)
    fields = set(type(result).model_fields)
    assert "margine_totale" not in fields
    assert "margine_complessivo" not in fields
    assert result.pnl.chiusi is not None
    assert result.pnl.in_corso is not None


def test_whether_the_period_can_still_move_is_on_the_response(rf01_corpus: Corpus) -> None:
    """Slice 4 §6.4: the only information that tells a reader whether the number can still
    change. Shown beside the total, never in a footnote."""
    result = _dashboard(rf01_corpus.engine)
    assert isinstance(result.pnl.periodo_chiuso, bool)
    assert isinstance(result.pnl.voci_scritte_in_ritardo, int)


def test_no_fiscal_field_appears_anywhere_on_this_dashboard(rf01_corpus: Corpus) -> None:
    """§5.3: the fiscal estimate stays at /app/analisi/fiscale, admin-only. A dashboard is
    the screen most likely to end up in a screenshot or a screen share. Checked by field
    name over the rendered JSON, not by intention."""
    result = _dashboard(rf01_corpus.engine)
    rendered = result.model_dump_json()
    for forbidden in (
        "imponibile_fiscale",
        "imposta_sostitutiva",
        "contributi",
        "netto_stimato",
        "coefficiente_redditivita",
        "aliquota_imposta_sostitutiva",
        "aliquota_inps",
    ):
        assert forbidden not in rendered, forbidden


def test_no_deal_level_margin_and_no_year_on_year_comparison() -> None:
    """§5.3's other two absences. Ranking deals by margin is the feature slice 4 §13
    refuses to enable by inertia, and a year-on-year comparison whose prior period is
    partly written is worse than none."""
    fields = set(EconomicDashboard.model_fields)
    for forbidden in ("deal_in_evidenza", "margine_per_deal", "anno_precedente", "confronto"):
        assert forbidden not in fields, forbidden
