"""Composition, and deliberately nothing else.

**This module contains no arithmetic, and `packages/core/tests/test_dashboard_no_arithmetic.py`
makes that a fact of the build rather than an intention of this docstring.** It may not
import `Decimal`, and it may not contain a `BinOp` node with `*`, `/` or `-`. A composition
service that cannot subtract cannot invent a margin.

Spec §3: every figure on a dashboard is either returned verbatim by the service that owns
the data, or a single `COUNT`/`SUM` written in the repository of the table it counts. So
this file calls **services** for figures somebody else already owns and **repositories**
for the aggregates it defines -- and never a third thing.

Why repositories rather than services for those aggregates: a service exists to own
authorisation, a transaction and business rules, and these aggregates have none beyond
`deleted_at IS NULL`. Putting `pipeline_summary` on `DealService` would create two paths an
agent could reach the same number by -- the deal domain tool and the dashboard tool -- which
is the duplication this slice exists not to introduce.

**One endpoint, one transaction, one instant, in `REPEATABLE READ`.** Not one endpoint per
card. In `READ COMMITTED` -- Postgres's default, and so what you get by saying nothing --
each statement takes its own snapshot, and seven queries in one transaction can see seven
states exactly as seven transactions can: a user who adds two cards by hand and does not
get the third stops trusting all three, and is right to. The transaction is read-only, so
the usual price of the higher level is not paid -- a serialisation failure can only strike a
writer, and nothing here writes.

The accepted cost, stated because it is real: no partial rendering. One slow figure slows
the whole page. It is bearable because the queries are few and the period is always bounded,
and it is the price of the property this page exists for.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.dashboard.schemas import (
    CassaAttesaMese,
    CommercialDashboard,
    EconomicDashboard,
    EsposizioneCliente,
    FasciaScadenza,
    FatturaScaduta,
    OperationalDashboard,
    PeriodoQuery,
    ReceivablesDashboard,
    Signal,
)
from pigrocrm.core.db import current_week, today_local, window_from
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.timetracking.repository import TimeEntryRepository

# The property §7.1 requires, named so the tests can assert on the same constant the code
# uses rather than on a duplicated string literal.
SNAPSHOT_ISOLATION = "REPEATABLE READ"

_PENDING_OFFERS_SHOWN = 20
_EXPECTED_CLOSURE_WINDOW_DAYS = 30
# §6.1: fifty rows, not paginated. A complete history is the entity's own timeline, which
# already exists; a paginated global feed would be a second way to browse the same rows.
_RECENT_ACTIVITIES = 50
# Slice 8 §2.1's six buckets, labelled once here: the code is the contract, the label is
# copy, and the page prints the label it is sent rather than keeping a second table.
_FASCE_ETICHETTE: dict[str, str] = {
    "scaduto": "Scaduto",
    "entro_30": "Entro 30 giorni",
    "da_31_a_60": "Da 31 a 60 giorni",
    "da_61_a_90": "Da 61 a 90 giorni",
    "oltre_90": "Oltre 90 giorni",
    "senza_scadenza": "Senza scadenza",
}
# The overdue bucket's drill-through: `?scadute=true` on the invoice list is
# `_overdue_predicate`, the predicate the bucket is summed with (criterion 2).
_SCADUTO_LINK = "/app/invoices?scadute=true"
# The concentration signal's own drill-through (REB-371): the economic tab, where
# every customer's share of the year's revenue is listed
# (`EconomicOverview.concentrazione_clienti`). Unlike the other three signals there is
# no separate filtered list of "customers over the threshold" to point at instead --
# the concentration table itself is the rows behind this count.
_CONCENTRAZIONE_LINK = "/app/?tab=economica"
_CLIENTI_SHOWN = 10
_SCADUTE_SHOWN = 50


class DashboardService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.deals = DealRepository(session)
        self.documents = DocumentRepository(session)
        self.analytics = AnalyticsService(session)
        self.analytics_repo = AnalyticsRepository(session)
        self.invoices = InvoiceRepository(session)
        self.entries = TimeEntryRepository(session)
        self.activities = ActivityRepository(session)

    def _open_snapshot(self) -> datetime:
        """Begin the one read-only `REPEATABLE READ` transaction, and return its instant.

        Must be the first thing that touches the session: Postgres refuses to change the
        isolation level once a transaction has begun. That is not automatic in either
        adapter, and both pay for it explicitly: the API's dashboard routes take
        `SnapshotSessionDep`, a *second* session per request, because `ActorDep` resolves
        the cookie by reading `users` on the ordinary one; the MCP server resolves its PAT
        in a short-lived session of its own before any tool body runs.

        It raises rather than continuing when it cannot. Silent degradation here produces a
        total that was true at no single instant, and nothing about re-reading this file
        would reveal it -- which makes a loud failure strictly better than a plausible
        number. SQLAlchemy accepts the execution option on a session that is already in a
        transaction and quietly applies it to the *next* one, so the check is explicit
        rather than a `try`/`except` around the call: the failure this guards against is
        precisely the one that raises nothing.

        `transaction_timestamp()` is constant for the whole transaction, so it is the
        instant the whole response describes, and this `SELECT` is also what fixes the
        snapshot: in `REPEATABLE READ` Postgres takes it at the transaction's first
        statement, so every figure below is read as of this line. On its own the timestamp
        would prove nothing -- it is constant in `READ COMMITTED` too, which is precisely
        why criterion 6 asserts the isolation level as well.
        """
        if self.session.in_transaction():
            raise RuntimeError(
                "a dashboard needs a session with no transaction in progress so it can "
                f"run in {SNAPSHOT_ISOLATION}; this session already had one. Pass a fresh "
                "session (the API's SnapshotSessionDep, not SessionDep, which the actor "
                "lookup has already read on)."
            )
        self.session.connection(execution_options={"isolation_level": SNAPSHOT_ISOLATION})
        # Annotated rather than returned directly: `scalar_one()` is typed `Any`, and an
        # `Any` flowing out of a function declared to return `datetime` is what `mypy`'s
        # `no-any-return` is for.
        istante: datetime = self.session.execute(
            text("SELECT transaction_timestamp()")
        ).scalar_one()
        return istante

    def get_commercial_dashboard(self, query: PeriodoQuery, actor: Actor) -> CommercialDashboard:
        """Pipeline snapshot plus two period measures. Touches no invoice and no hour --
        it reads `deals`, `pipeline_stages` and `documents`, which is what lets it ship
        before the economic dashboard (§4, §17).

        The period is resolved **before** the snapshot is opened: a malformed period is the
        caller's mistake, and answering it with a `ValidationFailed` should not cost a
        transaction.

        No authorisation check: §13 states this slice adds no role and no authorisation
        rule, every figure here comes from a read every role already has, and the one
        admin-only figure of that area -- the fiscal estimate -- is on no dashboard (§5.3).
        Inventing a fourth visibility level on a read-only screen would put a security rule
        where nobody looks for one. `actor` is taken because every service method here does.
        """
        periodo = query.resolve()
        calcolato_alle = self._open_snapshot()
        # The window the "prossime chiusure" card means starts where the period ends, not
        # where it begins: on a January dashboard the imminent closures are February's, not
        # January's own. `window_from` lives in `db/clock.py` because a date offset is still
        # a `BinOp` and this package may not contain one -- see the module docstring.
        _, finestra_a = window_from(periodo.a, _EXPECTED_CLOSURE_WINDOW_DAYS)
        return CommercialDashboard(
            periodo=periodo,
            calcolato_alle=calcolato_alle,
            pipeline=self.deals.pipeline_summary(),
            chiusure=self.deals.closed_in_period(periodo.da, periodo.a),
            offerte_in_attesa=self.documents.pending_offers(_PENDING_OFFERS_SHOWN),
            offerte_in_attesa_totale=self.documents.count_pending_offers(),
            chiusure_previste_30_giorni=self.deals.expected_closures(periodo.a, finestra_a),
            chiusure_non_attribuibili=self.deals.unattributable_closures(),
            offerte_accettate_deal_non_vinto=(self.documents.count_accepted_with_unwon_deal()),
        )

    def get_economic_dashboard(self, query: PeriodoQuery, actor: Actor) -> EconomicDashboard:
        """§5. Composition only: not one figure on this page is computed here.

        `period_pnl` is called with the same `actor` the caller supplied, so its own
        authorisation applies unchanged -- this method adds none and removes none. Its
        result is embedded verbatim rather than flattened, which is what makes criterion
        1's reconciliation an identity instead of a comparison: there is no field here for
        a P&L row to be renamed or recombined into on the way through.

        The receivable figures come from `InvoiceRepository` rather than from
        `InvoiceService`: §3 rule 2 puts a single-table `SUM` in that table's repository
        even when the table belongs to another slice, and adding a public method to
        `InvoiceService` would force either a new MCP tool or an edit to slice 3 §11's
        four-name exclusion list.

        `da_incassare` and `scaduto` take no period, and that asymmetry is deliberate
        rather than an omission: revenue is attributed to a period by `data_emissione`,
        while an invoice issued in February and still unpaid is money owed today whatever
        window the reader is looking at. Passing `periodo` to either would turn the one
        figure on this page that answers "what is outstanding" into a second, weaker
        rendering of "what was invoiced".

        The period is resolved **before** the snapshot is opened, as on the commercial
        dashboard: a malformed period is the caller's mistake and should not cost a
        transaction.
        """
        periodo = query.resolve()
        calcolato_alle = self._open_snapshot()
        return EconomicDashboard(
            periodo=periodo,
            calcolato_alle=calcolato_alle,
            pnl=self.analytics.period_pnl(
                PeriodPnlQuery(da=periodo.da, a=periodo.a, customer_id=None), actor
            ),
            da_incassare=self.invoices.sum_da_incassare(),
            scaduto=self.invoices.sum_scaduto(),
            fatture_emesse=self.invoices.count_emesse_in_periodo(periodo.da, periodo.a),
        )

    def get_operational_dashboard(self, actor: Actor) -> OperationalDashboard:
        """§6. **No period parameter**, deliberately: the current week and a backlog are the
        two things that make no sense in the past, so there is nothing here to get wrong --
        and it is why the backlog comes from `unbilled_backlog`, which has no period, rather
        than from `period_pnl`, which is by definition of one (§6.3).

        Four signals are built here as `Signal` rows, and that is composition and not
        arithmetic: each `conteggio` is a `COUNT` its own repository produced, and every
        label and link is a literal. `core/dashboard/` still contains no `*`, `/` or `-`,
        and `test_dashboard_no_arithmetic.py` is what confirms it rather than this sentence.
        The fourth, `concentrazione_sopra_soglia` (REB-371, §3 and §5 item 2 of
        `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`),
        keeps to the same rule: its COUNT, `AnalyticsRepository.count_over_concentration_
        threshold`, is the one place each customer's share is compared against
        `self.settings.concentrazione_soglia_preferita` -- that comparison stays in the
        repository, never here. Its link is the only one of the four that is not a
        matching filtered list: it points at the economic tab's own concentration table,
        because no separate "customers over the threshold" list exists to filter.

        None of the four is stored and none is a flag on a row -- they are predicates,
        evaluated on request. A stored signal is §1's second source of truth in disguise,
        and it would need somewhere to be recomputed from, which is the materialised summary
        §7 refuses.

        Every other `collegamento` names a filter that exists and that shares its predicate
        function with the count beside it (criterion 2), so a card and the list behind it
        cannot describe different rows: `invoiced_not_won_predicate`,
        `won_with_unbilled_hours_predicate` and `_overdue_predicate` each have exactly two
        callers, one per side.

        §6.2's own fourth signal is still not here: "offerta accettata, deal non vinto" is on
        the commercial dashboard, because it needs no invoices and therefore shipped with the
        automation it cross-checks (§17). REB-371's concentration signal is a different,
        later addition and does not fill that slot -- this page now carries four signals of
        its own regardless.

        `current_week()` and the calendar year are both read **before** the snapshot opens,
        like the period on the other two dashboards: neither needs a transaction, and it
        keeps the first statement of the session the one that fixes the snapshot.
        """
        da, a = current_week()
        anno = today_local().year
        calcolato_alle = self._open_snapshot()
        return OperationalDashboard(
            calcolato_alle=calcolato_alle,
            settimana=self.entries.week_hours(da, a),
            arretrato=self.analytics.unbilled_backlog(actor),
            segnali=[
                Signal(
                    codice="fatturato_non_vinto",
                    etichetta="Fatturato ma non vinto",
                    conteggio=self.invoices.count_deals_invoiced_not_won(),
                    collegamento="/app/deal/list?fatturato_non_vinto=true",
                ),
                Signal(
                    codice="vinto_da_fatturare",
                    etichetta="Vinto ma da fatturare",
                    conteggio=self.entries.count_won_deals_to_invoice(),
                    collegamento="/app/deal/list?da_fatturare=true",
                ),
                Signal(
                    codice="scaduto_non_incassato",
                    etichetta="Scaduto e non incassato",
                    conteggio=self.invoices.count_scadute_non_incassate(),
                    collegamento="/app/invoices?scadute=true",
                ),
                Signal(
                    codice="concentrazione_sopra_soglia",
                    etichetta="Concentrazione cliente sopra soglia",
                    conteggio=self.analytics_repo.count_over_concentration_threshold(
                        anno, self.settings.concentrazione_soglia_preferita
                    ),
                    collegamento=_CONCENTRAZIONE_LINK,
                ),
            ],
            attivita_recenti=[
                ActivityRead.model_validate(row)
                for row in self.activities.recent(_RECENT_ACTIVITIES)
            ],
        )

    def get_receivables_dashboard(self, actor: Actor) -> ReceivablesDashboard:
        """Slice 8 part A. **No period**, for the operational dashboard's reason: a
        receivable is owed today whatever window is on screen. Composition only: every sum,
        count and share below was produced by `InvoiceRepository`, and the labels and the
        one link are literals. `oggi` is read once, after the snapshot, and handed to every
        reading, so the four of them measure from the same day even across midnight.
        """
        calcolato_alle = self._open_snapshot()
        oggi = today_local()
        fasce = [
            FasciaScadenza(
                codice=row.codice,
                etichetta=_FASCE_ETICHETTE[row.codice],
                da=row.da,
                a=row.a,
                importo=row.importo,
                numero=row.numero,
                quota=row.quota,
                collegamento=_SCADUTO_LINK if row.codice == "scaduto" else None,
            )
            for row in self.invoices.ageing_receivables(oggi)
        ]
        scadute = [
            FatturaScaduta(**row._asdict())
            for row in self.invoices.overdue_with_reminders(oggi, _SCADUTE_SHOWN)
        ]
        return ReceivablesDashboard(
            calcolato_alle=calcolato_alle,
            oggi=oggi,
            totale=self.invoices.sum_da_incassare(),
            fasce=fasce,
            per_mese=[
                CassaAttesaMese(**row._asdict()) for row in self.invoices.receivables_by_due_month()
            ],
            per_cliente=[
                EsposizioneCliente(**row._asdict())
                for row in self.invoices.receivables_by_customer(oggi, _CLIENTI_SHOWN)
            ],
            scadute=scadute,
            scadute_totale=self.invoices.count_scadute_non_incassate(),
        )
