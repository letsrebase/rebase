"""Composition, and deliberately nothing else.

**This module contains no arithmetic, and `packages/core/tests/test_dashboard_no_arithmetic.py`
makes that a fact of the build rather than an intention of this docstring.** It may not
import `Decimal`, and it may not contain a `BinOp` node with `*`, `/` or `-`. A composition
service that cannot subtract cannot invent a margin.

Spec §3: every figure on a dashboard is either returned verbatim by the service that owns
the data, or a single `COUNT`/`SUM` written in the repository of the table it counts, or one
of the three declared exceptions, all of them in `deals/repository.py` (the weighted value,
the conversion rate and, since REB-931, the monthly recurring value, which prices the deal's
lines through `deals.value`). So this file calls **services** for figures somebody else
already owns and **repositories** for the aggregates it defines -- and never a third thing.

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

from collections.abc import Set as AbstractSet
from datetime import datetime
from functools import cached_property
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.activities.service import serve_activities
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.dashboard.schemas import (
    BandDueDate,
    CommercialDashboard,
    EconomicDashboard,
    ExposureCustomer,
    OperationalDashboard,
    OverdueInvoice,
    PensionFundPendingMonth,
    PeriodQuery,
    ReceivablesDashboard,
    Signal,
)
from pigrocrm.core.db import current_week, today_local, window_from
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.invoices.receivables import BAND_LABELS
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.proposals.access import visible_to as proposals_visible_to
from pigrocrm.core.timetracking.repository import TimeEntryRepository

# The property §7.1 requires, named so the tests can assert on the same constant the code
# uses rather than on a duplicated string literal.
SNAPSHOT_ISOLATION = "REPEATABLE READ"

_PENDING_OFFERS_SHOWN = 20
_EXPECTED_CLOSURE_WINDOW_DAYS = 30
# §6.1: fifty rows, not paginated. A complete history is the entity's own timeline, which
# already exists; a paginated global feed would be a second way to browse the same rows.
_RECENT_ACTIVITIES = 50
# The same feed for everyone else is fifteen rows: an agent needs what happened lately, not
# fifty rows of identifiers (REB-811).
_RECENT_ACTIVITIES_FILTERED = 15
# REB-811: what the feed shows anyone but a person administering the space. Business
# entities only. Account events (`user`: tokens created and first used; `invitation`, with
# the invitee's address; `digest`; the Slack, Drive and settings rows) are the admin's, and
# an agent's token never gets them, whatever the owner's role: it is the credential that
# reads untrusted text, and the feed would hand it addresses and token ids to relay.
_BUSINESS_ENTITIES: frozenset[str] = frozenset(
    {
        "organization",
        "person",
        "deal",
        "document",
        "invoice",
        "contract",
        "contract_expense",
        "task",
        "work_unit",
        "approval",
        "proposal",
        "time_entry",
        "purchase",
    }
)
# Slice 8 §2.1's six buckets are labelled once, in `invoices/receivables.py`: the code is the
# contract, the label is copy, and the page prints the label it is sent.
_BAND_LABELS = BAND_LABELS
# The overdue bucket's drill-through: `?overdue=true` on the invoice list is
# `_overdue_predicate`, the predicate the bucket is summed with (criterion 2).
_EXPIRED_LINK = "/app/invoices?overdue=true"
# The concentration signal's own drill-through (REB-371): the economic tab, where
# every customer's share of the year's revenue is listed
# (`EconomicOverview.customer_concentration`). Unlike the other three signals there is
# no separate filtered list of "organizations over the threshold" to point at instead --
# the concentration table itself is the rows behind this count.
_CONCENTRATION_LINK = "/app?tab=economic"
_ORGANIZATIONS_SHOWN = 10
_OVERDUE_SHOWN = 50


# The modules a signal of the operational dashboard reads; a signal with no row is the core's.
_SIGNAL_MODULES: dict[str, tuple[str, ...]] = {
    "invoiced_unwon": ("deals",),
    "won_to_invoice": ("deals", "time"),
}
# The activity rows of each module's records: an off module's history is not in the feed.
_MODULE_ENTITIES: dict[str, frozenset[str]] = {
    "deals": frozenset({"deal"}),
    "contracts": frozenset({"contract", "contract_expense", "work_unit", "approval"}),
    "time": frozenset({"time_entry"}),
    "purchases": frozenset({"purchase"}),
    "taxes": frozenset({"declared_tax_figure"}),
    "staff": frozenset({"staff_member", "payroll_settings"}),
    "collaborators": frozenset({"engagement"}),
}


def signals_for(signals: list[Signal], modules: AbstractSet[str]) -> list[Signal]:
    """`modules` is what the space has on: a signal about a module that is off would link to
    a page that answers 404 (spec 2026-11-09, A2)."""
    return [s for s in signals if set(_SIGNAL_MODULES.get(s.code, ())) <= modules]


class DashboardService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # Each dashboard reads a different subset of these, so they are built on first use
    # rather than all seven for every request.
    @cached_property
    def _deals(self) -> DealRepository:
        return DealRepository(self.session)

    @cached_property
    def _documents(self) -> DocumentRepository:
        return DocumentRepository(self.session)

    @cached_property
    def _analytics(self) -> AnalyticsService:
        return AnalyticsService(self.session)

    @cached_property
    def _analytics_repo(self) -> AnalyticsRepository:
        return AnalyticsRepository(self.session)

    @cached_property
    def _invoices(self) -> InvoiceRepository:
        return InvoiceRepository(self.session)

    @cached_property
    def _entries(self) -> TimeEntryRepository:
        return TimeEntryRepository(self.session)

    @cached_property
    def _activities(self) -> ActivityRepository:
        return ActivityRepository(self.session)

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
        instant: datetime = self.session.execute(
            text("SELECT transaction_timestamp()")
        ).scalar_one()
        return instant

    def get_commercial_dashboard(self, query: PeriodQuery, actor: Actor) -> CommercialDashboard:
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
        period = query.resolve()
        calculated_at = self._open_snapshot()
        # The window the "prossime chiusure" card means starts where the period ends, not
        # where it begins: on a January dashboard the imminent closures are February's, not
        # January's own. `window_from` lives in `db/clock.py` because a date offset is still
        # a `BinOp` and this package may not contain one -- see the module docstring.
        _, window_to = window_from(period.date_to, _EXPECTED_CLOSURE_WINDOW_DAYS)
        # The legal entity is one more argument of every read and nothing else (REB-630, spec
        # 2026-10-03 §1.9): each repository adds it as a predicate, so «tutte», which
        # sends none, is the sum of the per-legal entity answers by construction.
        legal_entity = query.legal_entity_id
        return CommercialDashboard(
            period=period,
            calculated_at=calculated_at,
            legal_entity_id=legal_entity,
            pipeline=self._deals.pipeline_summary(legal_entity),
            closings=self._deals.closed_in_period(period.date_from, period.date_to, legal_entity),
            open_monthly_recurring=self._deals.open_monthly_recurring(legal_entity),
            offers_pending=self._documents.pending_offers(_PENDING_OFFERS_SHOWN, legal_entity),
            offers_pending_total=self._documents.count_pending_offers(legal_entity),
            expected_closings_30_days=self._deals.expected_closures(
                period.date_to, window_to, legal_entity
            ),
            closings_not_attributable=self._deals.unattributable_closures(legal_entity),
            accepted_offers_deal_not_won=(
                self._documents.count_accepted_with_unwon_deal(legal_entity)
            ),
        )

    def get_economic_dashboard(
        self,
        query: PeriodQuery,
        actor: Actor,
        modules: AbstractSet[str] | None = None,
    ) -> EconomicDashboard:
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

        `unpaid` and `expired` take no period, and that asymmetry is deliberate
        rather than an omission: revenue is attributed to a period by `issue_date`,
        while an invoice issued in February and still unpaid is money owed today whatever
        window the reader is looking at. Passing `period` to either would turn the one
        figure on this page that answers "what is outstanding" into a second, weaker
        rendering of "what was invoiced".

        The period is resolved **before** the snapshot is opened, as on the commercial
        dashboard: a malformed period is the caller's mistake and should not cost a
        transaction.

        `modules` is what the space has on (`None`: everything), handed to `period_pnl`: without
        `time` the labour and the hours are `None` (spec 2026-11-09, A2).
        """
        period = query.resolve()
        calculated_at = self._open_snapshot()
        legal_entity = query.legal_entity_id
        return EconomicDashboard(
            period=period,
            calculated_at=calculated_at,
            legal_entity_id=legal_entity,
            pnl=self._analytics.period_pnl(
                PeriodPnlQuery(
                    date_from=period.date_from,
                    date_to=period.date_to,
                    customer_id=None,
                    legal_entity_id=legal_entity,
                ),
                actor,
                modules,
            ),
            unpaid=self._invoices.sum_unpaid(legal_entity),
            expired=self._invoices.sum_expired(legal_entity),
            issued_invoices=self._invoices.count_issued_in_period(
                period.date_from, period.date_to, legal_entity
            ),
        )

    def _recent_feed(self, actor: Actor, modules: AbstractSet[str] | None) -> list[Activity]:
        off = (
            frozenset().union(
                *(rows for key, rows in _MODULE_ENTITIES.items() if key not in modules)
            )
            if modules is not None
            else frozenset()
        )
        visible = proposals_visible_to(actor)
        if actor.type == "user" and actor.can_administer:
            return self._activities.recent(_RECENT_ACTIVITIES, None, off, visible)
        return self._activities.recent(
            _RECENT_ACTIVITIES_FILTERED, _BUSINESS_ENTITIES, off, visible
        )

    def get_operational_dashboard(
        self,
        actor: Actor,
        legal_entity_id: UUID | None = None,
        modules: AbstractSet[str] | None = None,
    ) -> OperationalDashboard:
        """§6. **No period parameter**, deliberately: the current week and a backlog are the
        two things that make no sense in the past, so there is nothing here to get wrong --
        and it is why the backlog comes from `unbilled_backlog`, which has no period, rather
        than from `period_pnl`, which is by definition of one (§6.3).

        Four signals are built here as `Signal` rows, and that is composition and not
        arithmetic: each `count` is a `COUNT` its own repository produced, and every
        label and link is a literal. `core/dashboard/` still contains no `*`, `/` or `-`,
        and `test_dashboard_no_arithmetic.py` is what confirms it rather than this sentence.
        The fourth, `concentration_above_threshold` (REB-371, §3 and §5 item 2 of
        `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`),
        keeps to the same rule: its COUNT, `AnalyticsRepository.count_over_concentration_
        threshold`, is the one place each customer's share is compared against
        `self.settings.preferred_concentration_threshold` -- that comparison stays in the
        repository, never here. Its link is the only one of the four that is not a
        matching filtered list: it points at the economic tab's own concentration table,
        because no separate "organizations over the threshold" list exists to filter.

        None of the four is stored and none is a flag on a row -- they are predicates,
        evaluated on request. A stored signal is §1's second source of truth in disguise,
        and it would need somewhere to be recomputed from, which is the materialised summary
        §7 refuses.

        Every other `link` names a filter that exists and that shares its predicate
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

        `legal_entity_id` (REB-630, spec 2026-10-03 §1.9) is one more argument of every
        count, and the links do not carry it: each drill-through list already follows
        the sidebar's selection on its own. The concentration signal is the one count
        whose «tutte» is not a plain sum: `count_over_concentration_threshold` reads
        each share against its own legal entity's revenue and counts the distinct organizations
        over the threshold in any of them, so a customer who is a third of the SRL and
        a tenth of the whole still counts. The recent feed stays the space's: an
        activity row has no legal entity.
        """
        date_from, date_to = current_week()
        year = today_local().year
        calculated_at = self._open_snapshot()
        board = OperationalDashboard(
            calculated_at=calculated_at,
            legal_entity_id=legal_entity_id,
            week=(
                self._entries.week_hours(date_from, date_to, legal_entity_id)
                if modules is None or "time" in modules
                else None
            ),
            backlog=self._analytics.unbilled_backlog(actor, legal_entity_id, modules),
            signals=[
                Signal(
                    code="invoiced_unwon",
                    label="Fatturato ma non vinto",
                    count=self._invoices.count_deals_invoiced_not_won(legal_entity_id),
                    link="/app/deal/list?invoiced_unwon=true",
                ),
                Signal(
                    code="won_to_invoice",
                    label="Vinto ma da fatturare",
                    count=self._entries.count_won_deals_to_invoice(legal_entity_id),
                    link="/app/deal/list?to_invoice=true",
                ),
                Signal(
                    code="overdue_unpaid",
                    label="Scaduto e non incassato",
                    count=self._invoices.count_overdue_not_paid(legal_entity_id),
                    link="/app/invoices?overdue=true",
                ),
                Signal(
                    code="concentration_above_threshold",
                    label="Concentrazione cliente sopra soglia",
                    count=self._analytics_repo.count_over_concentration_threshold(
                        year, self.settings.preferred_concentration_threshold, legal_entity_id
                    ),
                    link=_CONCENTRATION_LINK,
                ),
            ],
            recent_activity=serve_activities(self.session, self._recent_feed(actor, modules)),
        )
        if modules is not None:
            board.signals = signals_for(board.signals, modules)
        return board

    def get_receivables_dashboard(
        self, actor: Actor, legal_entity_id: UUID | None = None
    ) -> ReceivablesDashboard:
        """Slice 8 part A. **No period**, for the operational dashboard's reason: a
        receivable is owed today whatever window is on screen. Composition only: every sum,
        count and share below was produced by `InvoiceRepository`, and the labels and the
        one link are literals. `today` is read once, after the snapshot, and handed to every
        reading, so the four of them measure from the same day even across midnight.
        """
        calculated_at = self._open_snapshot()
        today = today_local()
        bands = [
            BandDueDate(
                code=row.code,
                label=_BAND_LABELS[row.code],
                date_from=row.date_from,
                date_to=row.date_to,
                amount=row.amount,
                number=row.number,
                share=row.share,
                link=_EXPIRED_LINK if row.code == "expired" else None,
            )
            for row in self._invoices.ageing_receivables(today, legal_entity_id)
        ]
        overdue = [
            OverdueInvoice(**row._asdict())
            for row in self._invoices.overdue_with_reminders(today, _OVERDUE_SHOWN, legal_entity_id)
        ]
        return ReceivablesDashboard(
            calculated_at=calculated_at,
            today=today,
            legal_entity_id=legal_entity_id,
            total=self._invoices.sum_unpaid(legal_entity_id),
            bands=bands,
            by_month=[
                PensionFundPendingMonth(**row._asdict())
                for row in self._invoices.receivables_by_due_month(legal_entity_id)
            ],
            by_customer=[
                ExposureCustomer(**row._asdict())
                for row in self._invoices.receivables_by_customer(
                    today, _ORGANIZATIONS_SHOWN, legal_entity_id
                )
            ],
            overdue=overdue,
            total_overdue=self._invoices.count_overdue_not_paid(legal_entity_id),
        )
