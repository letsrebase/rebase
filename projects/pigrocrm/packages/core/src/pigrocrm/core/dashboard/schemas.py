"""What a dashboard returns. Sub-plan 6C appends two more dashboards to this file.

Every money field is `Decimal` with `max_digits`/`decimal_places` matching the column it
came from, and every one arrives already summed. The frontend formats; it never adds
(§13, and Task B13's AST test).

This is the one module of `core/dashboard/` allowed to import `Decimal`, and Task B9's
scan names it as such: declaring a `Decimal` field performs no arithmetic, and the clause
exists to forbid arithmetic. Every other module in the package -- `service.py` and
whatever 6C adds -- may neither import `Decimal` nor contain a `*`, `/` or `-` operator,
so that a composition layer provably cannot invent a figure.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.analytics.schemas import PeriodPnl, UnbilledBacklog
from pigrocrm.core.db import month_bounds, today_local, window_from
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.pipeline.schemas import StageKind

# Ten years and a bit -- the span of the §16 reference corpus. A ceiling exists because
# §7.3 requires the predicate to always carry a bounded period: without one,
# `da=0001-01-01` is a full table scan requested from a query string.
MAX_PERIOD_DAYS = 3660


class Periodo(BaseModel):
    """Normalised and echoed back, always. A screenshot of a dashboard with no explicit
    period is a number with no unit (§4)."""

    da: date
    a: date


class PeriodoQuery(BaseModel):
    """The period, or nothing at all.

    Both bounds or neither. Supplying one and letting the service guess the other would
    silently answer a different question from the one asked, and the reader would have no
    way to see it -- the response echoes the period back for exactly this reason.
    """

    model_config = ConfigDict(extra="forbid")

    da: date | None = None
    a: date | None = None

    def resolve(self) -> Periodo:
        """The normalised period, or a named `ValidationFailed`.

        The `field` of every error here is the bound the caller must change, because
        `fieldErrorFrom` in the web client and an MCP agent both read it.
        """
        if (self.da is None) != (self.a is None):
            raise ValidationFailed(
                "periodo",
                "da" if self.da is None else "a",
                "il periodo richiede entrambe le date, o nessuna",
                expected="da e a insieme, oppure nessuna delle due",
            )
        if self.da is None or self.a is None:
            today = today_local()
            first, last = month_bounds(today.year, today.month)
            return Periodo(da=first, a=last)
        if self.da > self.a:
            raise ValidationFailed(
                "periodo",
                "da",
                "la data iniziale è successiva a quella finale",
                expected=f"da <= {self.a.isoformat()}",
            )
        # The ceiling is expressed as "the last day still admitted" rather than as
        # `(self.a - self.da).days`, for two reasons that happen to agree: a `-` anywhere
        # under `core/dashboard/` is forbidden without exception (§3, and
        # `test_dashboard_no_arithmetic.py`, whose BinOp exemption list is empty and
        # includes this file), and `window_from` is the same inclusive convention every
        # other period in slices 4 and 6 uses. A period of exactly MAX_PERIOD_DAYS days'
        # difference is admitted; the next day is not.
        _, ultimo_ammesso = window_from(self.da, MAX_PERIOD_DAYS)
        if self.a > ultimo_ammesso:
            raise ValidationFailed(
                "periodo",
                "a",
                "periodo troppo lungo",
                expected=f"al massimo {MAX_PERIOD_DAYS} giorni",
            )
        return Periodo(da=self.da, a=self.a)


class PipelineStageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stage_id: str
    stage_code: str | None
    stage_nome: str
    # Which kind of stage this is, so a renderer can group the closed ones without
    # matching `stage_nome` -- a label the user is free to change (residuo R15). The same
    # reasoning that made `tipo` exist on `PipelineStage` in the first place.
    stage_tipo: StageKind
    posizione: int
    numero: int
    valore_totale: Decimal = Field(max_digits=12, decimal_places=2)
    # Counted, never summed as zero (§4). A missing expected value is not a value of zero,
    # and a reader has no way to tell the two apart from a total alone.
    senza_valore: int
    # §3 exception 1. Labelled "stima" in every rendering and never added to revenue.
    valore_ponderato: Decimal = Field(max_digits=12, decimal_places=2)


class ClosedInPeriod(BaseModel):
    vinti: int
    persi: int
    # `Σ valore_previsto` of the deals won in the period. **Not revenue** and not
    # comparable with it: it is what the deal *claimed*. The revenue of those same deals
    # is on the economic dashboard, and the two figures live on two pages for exactly this
    # reason (§4).
    valore_vinto: Decimal = Field(max_digits=12, decimal_places=2)
    # §3 exception 2. Per cent, two places. `None` -- never `0` -- when nothing closed:
    # zero per cent means "I lost everything", no closed deals means something else. Same
    # rule as slice 4 §7.1's margin percentage, computed by the same `money.percentage_of`
    # so that it is one rule and not two that agree today.
    tasso_conversione: Decimal | None = Field(default=None, max_digits=5, decimal_places=2)


class PendingOffer(BaseModel):
    document_id: str
    titolo: str
    deal_id: str | None
    customer_id: str | None
    stato_dal: date | None
    # `None`, not 0, when `stato_dal` is unknown: zero days would read as "sent today".
    giorni: int | None


class CommercialDashboard(BaseModel):
    """One endpoint, one transaction, one instant (§7.1).

    `calcolato_alle` is the `transaction_timestamp()` of *that* transaction, and the
    browser shows its age. A number with no age is a number the user believes is
    instantaneous.
    """

    periodo: Periodo
    calcolato_alle: datetime
    pipeline: list[PipelineStageSummary]
    chiusure: ClosedInPeriod
    offerte_in_attesa: list[PendingOffer]
    offerte_in_attesa_totale: int
    chiusure_previste_30_giorni: int
    # §4.1: deals closed before `chiuso_il` existed cannot be attributed to a period. The
    # dashboard declares how many rather than counting them as zero.
    chiusure_non_attribuibili: int
    # §6.2's first signal, and the permanent cross-check on automation A1.
    offerte_accettate_deal_non_vinto: int


class EconomicDashboard(BaseModel):
    """§5. **No new aggregate exists on this page.** Every figure comes from
    `AnalyticsService` or from `InvoiceRepository`.

    `pnl` embeds the owning service's own model verbatim rather than flattening its fields
    into this one: a flattened copy is a place for a field to be renamed, reordered or
    quietly recombined, and embedding it makes criterion 1's reconciliation an identity
    instead of a comparison.

    `da_incassare` and `scaduto` are the one pair here that does **not** come from the
    P&L, and they use `totale` rather than `imponibile` because they are a different
    quantity: a receivable is what must arrive in the bank, VAT included -- money
    collected on the State's behalf. Neither enters any margin, and neither shares a total
    row with revenue (§5.2).

    Deliberately absent (§5.3): any fiscal estimate field, any single deal's margin, and
    any comparison with the same period last year. The fiscal estimate stays at
    `/app/analisi/fiscale`, admin-only, and the dashboard shows a link and not a number --
    a dashboard is the screen most likely to end up in a screenshot or a screen share.
    """

    periodo: Periodo
    calcolato_alle: datetime
    pnl: PeriodPnl
    # No period: an invoice issued in February and still unpaid is still owed in March.
    da_incassare: Decimal = Field(max_digits=12, decimal_places=2)
    # A subset of `da_incassare`, rendered as one -- indented beneath it, never as a
    # second addable line.
    scaduto: Decimal = Field(max_digits=12, decimal_places=2)
    # A COUNT on the same predicate the revenue figure uses, so the two cannot describe
    # different sets.
    fatture_emesse: int


class DayHours(BaseModel):
    """One point of the week's series. Always present, even at `0.00`."""

    giorno: date
    ore: Decimal = Field(max_digits=8, decimal_places=2)


class WeekHours(BaseModel):
    """§6's first two rows, assembled whole by `TimeEntryRepository`.

    `giorni` always holds one entry per day of the window, zeros included and in calendar
    order: a series that silently omits its empty days is a chart that lies about its own
    shape, and a week with three worked days would render as three consecutive bars.

    `giorni_senza_ore` is a field and not something the client derives. Deriving it would
    put a set difference in the browser -- business logic in the frontend, over a set the
    browser would have to reconstruct from a range it was never told -- and the only
    server-side alternative, `DashboardService`, may contain no arithmetic at all (§3).

    `giorni_senza_ore` means "no entry was written for this day", not "these hours sum to
    zero". The two readings coincide today because `ck_time_entries_ore_range` is
    `ore > 0 AND ore <= 24`, so a day that has rows cannot total zero -- but they are not
    the same statement, and the first is the one that survives the constraint being
    loosened. Telling somebody who entered a day that they forgot it is the one way this
    figure can be actively unhelpful.
    """

    da: date
    a: date
    giorni: list[DayHours]
    # The real failure slice 4 §13 names when it refuses a stopwatch: "non ho mai inserito
    # martedì". This is the figure that attacks it, and the `ore-da-registrare` prompt of
    # §10 reads the same field.
    giorni_senza_ore: list[date]
    ore_totali: Decimal = Field(max_digits=10, decimal_places=2)


class Signal(BaseModel):
    """One of §6.2's inconsistency counts.

    Not stored, not a flag on a row: a predicate, evaluated on request. A stored signal is
    §1's second source of truth wearing a disguise, and it would need somewhere to be
    recomputed from -- which is the materialised summary §7 refuses.

    `collegamento` is mandatory in practice: a count with no way to see the rows behind it
    is a number nobody can act on, and §7.2's guarantee is that the count and that list are
    the same predicate. It is typed optional because a signal without a drill-through is a
    thinkable future shape and a lie in the type system is worse than an assertion in a
    test; `test_dashboard_operational.py` is what requires all three to have one today.
    """

    codice: str
    etichetta: str
    conteggio: int
    collegamento: str | None


class OperationalDashboard(BaseModel):
    """§6. "What do I have to do now."

    **No period.** Its figures are the current week and a backlog, which are the two things
    that make no sense in the past -- and it is why the backlog cannot come from
    `period_pnl`, which is by definition of a period (§6.3).

    **No new economic total.** The only money here is `arretrato.valore_maturato`, which
    belongs to `AnalyticsService` and is labelled accrued value, never revenue.

    Three signals, not four: "offerta accettata, deal non vinto" is on the *commercial*
    dashboard, because it needs no invoices and therefore shipped in the same sub-plan as
    the automation it cross-checks (§17).
    """

    calcolato_alle: datetime
    settimana: WeekHours
    arretrato: UnbilledBacklog
    segnali: list[Signal]
    attivita_recenti: list[ActivityRead]


class FasciaScadenza(BaseModel):
    """One of the six ageing buckets of slice 8 §2.1, as `InvoiceRepository.ageing_receivables`
    returns it: the sum and the count of the receivables whose due date falls in the
    bucket. `da`/`a` are the bucket's own bounds, both `None` for «senza scadenza» and
    `a` `None` for «oltre 90»; `quota` is the share of the largest bucket, in [0, 1],
    computed there so the page scales a bar without coercing an amount.

    `collegamento` is the drill-through, on the one bucket a list can answer today: the
    overdue one, which is `?scadute=true` on the invoice list and the same
    `_overdue_predicate` (criterion 2). The others carry `None` until the list takes a
    due-date window."""

    codice: str
    etichetta: str
    da: date | None
    a: date | None
    importo: Decimal = Field(max_digits=12, decimal_places=2)
    numero: int
    quota: float
    collegamento: str | None


class CassaAttesaMese(BaseModel):
    """What is owed in one month of `data_scadenza`. `mese` is the month's first day."""

    mese: date
    importo: Decimal = Field(max_digits=12, decimal_places=2)
    numero: int
    quota: float


class EsposizioneCliente(BaseModel):
    """One customer's unpaid total, and how much of it is already past due."""

    customer_id: UUID
    ragione_sociale: str
    importo: Decimal = Field(max_digits=12, decimal_places=2)
    numero: int
    scaduto: Decimal = Field(max_digits=12, decimal_places=2)
    quota: float


class FatturaScaduta(BaseModel):
    """One overdue receivable, with the reminders that actually left for it. Sent, not
    prepared: `PaymentReminder.sent_at` is what counts, so a draft still sitting in the
    mailbox does not read as a letter the customer ignored."""

    invoice_id: UUID
    numero: str
    customer_id: UUID
    cliente: str
    data_scadenza: date
    giorni_di_ritardo: int
    importo: Decimal = Field(max_digits=12, decimal_places=2)
    solleciti_inviati: int
    ultimo_sollecito_il: date | None


class ReceivablesDashboard(BaseModel):
    """Slice 8 part A (REB-329): when the money already invoiced arrives.

    **No period**, like the operational dashboard: a receivable is owed today whatever
    window the reader is looking at, and `oggi` is the one date every bucket is measured
    from. **No new data**: every figure is a `SUM` or a `COUNT` over `_receivable_filter`
    in `InvoiceRepository`, and the six buckets add up to `totale`, which is
    `sum_da_incassare` -- the same figure the economic dashboard prints, read the same
    way, which is what `test_dashboard_receivables.py` pins to the cent (§2.2).
    """

    calcolato_alle: datetime
    oggi: date
    totale: Decimal = Field(max_digits=12, decimal_places=2)
    fasce: list[FasciaScadenza]
    per_mese: list[CassaAttesaMese]
    per_cliente: list[EsposizioneCliente]
    scadute: list[FatturaScaduta]
    # How many overdue rows exist, against the `len(scadute)` actually listed.
    scadute_totale: int
