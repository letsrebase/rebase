"""Every shape this slice's reports return.

Declared together, and ahead of the methods that build them (tasks 4B-5 to 4B-8), so
that the vocabulary of the whole slice is readable in one file: `ricavi` means the same
thing in `DealPnl`, `PnlTotals` and `FiscalEstimate`, and a second meaning would have to
be introduced here, in the open, rather than discovered in a service.

Every figure arrives already summed. §6 forbids the frontend of this slice from
computing any economic total at all -- the previous system's whole P&L lived in `App.jsx`, with
three fiscal constants and float hour sums.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.fiscal.pack import CeilingConsequence

# Mirrors `Deal`'s own Numeric(p, s) column widths (`deals/models.py`), the same
# reasoning `deals/schemas.py`'s own constants give for existing at all: a value
# beyond a column's capacity would otherwise sail past Pydantic and reach `flush()`
# as a raw `DataError`. Declared locally rather than imported from `deals.schemas`:
# `pigrocrm.core.deals`'s own `__init__.py` imports `DealService`, which reaches
# `dashboard.schemas`, which imports back from this very module -- the same
# reasoning `auth/schemas.py` already gives for declaring `tariffa_oraria_default`'s
# own width locally rather than importing it.
CEILING_ORE_MAX_DIGITS = 8
CEILING_VALORE_MAX_DIGITS = 12
CEILING_DECIMAL_PLACES = 2
CEILING_FACTOR_MAX_DIGITS = 12
CEILING_FACTOR_DECIMAL_PLACES = 6

# The same three values as `DealTimeSummary.stato`, and deliberately a separate
# declaration rather than an import: `timetracking` answers "what is the state of these
# hours" and `analytics` answers "what is the state of this deal's economics". They
# coincide today because the second is derived from the first, and the day they stop
# coinciding a shared alias would silently pick a side.
DealStato = Literal["in corso", "da fatturare", "chiuso"]


class DealPnl(BaseModel):
    """One deal's profit and loss, every figure computed by the service and returned
    already summed -- the browser adds nothing (§6).

    `valore_maturato` is deliberately a separate field from `ricavi` and not a variant of
    it: it is the estimate of §3 decision 2, it is **not revenue**, it enters no P&L row,
    and it lives under a heading of its own. On an `in corso` deal it is the honest number
    to look at and the margin is provisional; the margin is only reportable in the
    `chiuso` state.
    """

    deal_id: UUID
    stato: DealStato
    ricavi: Decimal
    costi_diretti: Decimal
    costo_lavoro: Decimal
    margine_lordo: Decimal
    # `None`, never `0.00`, when revenue is zero: zero per cent means "everything I
    # earned went out in costs", a zero denominator means nothing has been earned. Two
    # different facts (§7.1, criterion 6).
    margine_percentuale: Decimal | None
    ore_totali: Decimal
    ore_fatturabili_non_fatturate: Decimal
    valore_maturato: Decimal
    ore_senza_tariffa: int
    fatture_emesse: int


# Which date puts an invoice's revenue in a period. `emissione` is the recorded decision
# (§7.1: revenue is what was invoiced, by the document's own date) and the default; the
# fiscal reports never take the other. `competenza` reads the same revenue by the accrual
# period the document declares, `coalesce(competenza_da, data_emissione)`, because
# invoicing runs late and August's work issued in September must be readable in August
# (ORB-61; `docs/design/DECISIONS.md`, 2026-09-09). Costs and hours ignore it: each
# quantity is attributed by its own date whatever the base.
RevenueBase = Literal["emissione", "competenza"]

# Which month a document's money belongs to in the cash view (ORB-133). `competenza` is
# the default Ivan asked for: every invoice, paid or not, and every draft or proforma
# sits in the month of the accrual period it declares, `coalesce(competenza_da,
# data_emissione)`, the same rule `RevenueBase` "competenza" uses. `incasso` is the
# reading the charts had before: a paid invoice by `data_incasso`, an unpaid one by
# `data_scadenza`, a draft or proforma by its own document date. Costs are by their own
# date under both. The fiscal estimate never takes this base: it is on the money.
CashBase = Literal["competenza", "incasso"]


class PeriodPnlQuery(BaseModel):
    da: date
    a: date
    customer_id: UUID | None = None
    base: RevenueBase = "emissione"


class PnlTotals(BaseModel):
    ricavi: Decimal
    costi_diretti: Decimal
    costo_lavoro: Decimal
    margine_lordo: Decimal
    margine_percentuale: Decimal | None
    deal: int


class PeriodPnl(BaseModel):
    """Two columns, never one total (§7.4).

    Adding a finished job's margin to a half-done one produces a figure that is neither,
    and that changes every week for reasons which are not business performance. The
    reportable number is `chiusi`.
    """

    da: date
    a: date
    customer_id: UUID | None
    # Echoed from the query, so a reader of the figure knows which of the two readings
    # of revenue produced it (ORB-61) without keeping the request beside the response.
    base: RevenueBase
    chiusi: PnlTotals
    in_corso: PnlTotals
    # A cost with `deal_id IS NULL`: it enters the period P&L in a row of its own and is
    # **never apportioned** onto any deal. Every apportionment key has one precise and
    # unacceptable consequence -- a deal's margin would move when a *different* deal was
    # invoiced.
    spese_generali: Decimal
    # Added by slice 6 §5: the informative rows the economic dashboard shows under a
    # heading that is not "ricavi", and which enter no margin. Same names as `DealPnl`'s
    # because they are the same quantity on a different object; the scope lives in the
    # label ("nel periodo" here, "in totale" on the operational dashboard), never in the
    # field name, and §5 forbids the two ever being rendered side by side.
    #
    # One difference from `DealPnl` is deliberate and is recorded here because the names
    # are identical: `DealPnl.valore_maturato` is `ricavi + valore delle ore non
    # fatturate`, an estimate of what the deal will finally have been worth. There is no
    # such reading for a period -- a period's revenue is already its own reported row, and
    # adding it in again would put revenue under a heading that says "non sono ricavi".
    # So this one is the accrued value of the unbilled billable hours **alone**, which is
    # what the card that prints it is labelled: "Valore maturato non fatturato".
    valore_maturato: Decimal = Field(max_digits=12, decimal_places=2)
    ore_fatturabili_non_fatturate: Decimal = Field(max_digits=8, decimal_places=2)
    # A count of entries, not a quantity of hours, exactly like `DealPnl.ore_senza_tariffa`
    # -- and scoped to the same rows as the two figures above it, so the three can be
    # reconciled with each other on the card that shows them together.
    ore_senza_tariffa: int
    # Whether the number can still move, which is the thing a reader most needs to know
    # and costs a COUNT over two columns that already exist (§6.4).
    periodo_chiuso: bool
    voci_scritte_in_ritardo: int


class UnbilledBacklog(BaseModel):
    """What is waiting to be invoiced, with no period.

    Separate from `PeriodPnl`'s three same-named fields on purpose (§5): those are the
    period's figures, these are the total. Two different numbers with the same name is the
    fastest way to lose a reader's trust, so the *labels* carry the scope -- "nel periodo"
    on the economic dashboard, "in totale" on the operational one -- and the two are never
    rendered side by side.
    """

    ore_fatturabili_non_fatturate: Decimal = Field(max_digits=8, decimal_places=2)
    # `Σ ROUND(ore × tariffa_applicata, 2)` over `time_entries` -- slice 4 §7.3's
    # formula, computed here because §3 forbids `core/dashboard/` any multiplication at
    # all -- plus REB-372's own contribution from approved-or-later `work_units`, priced
    # against their own contract's rate card. It is **not** revenue and enters no
    # margin: the revenue is the invoice.
    valore_maturato: Decimal = Field(max_digits=12, decimal_places=2)
    # A rate of zero and no rate are different facts (slice 4 §5.1); the same holds for
    # a `work_unit` day whose date has no rate card in force (REB-372). Every row here
    # contributes nothing to `valore_maturato`; only the `time_entries` half also
    # contributes to `ore_fatturabili_non_fatturate` -- a `work_unit`'s own quantity is
    # priced in whatever unit its rate card names, not always an hour.
    voci_senza_tariffa: int
    voci: int


class BudgetQuery(BaseModel):
    da: date
    a: date
    customer_id: UUID | None = None
    # Mandatory pagination from the first commit (residual B3): the margins view is by
    # its nature a list of *closed* deals, so unbounded growth stops being invisible here.
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class BudgetVsActualRow(BaseModel):
    deal_id: UUID
    nome: str
    ore_preventivate: Decimal | None
    ore_consuntivate: Decimal
    valore_preventivato: Decimal | None
    ricavi: Decimal
    # Per cent, two places: 40 hours of an estimated 100 is `"40.00"`.
    avanzamento_ore: Decimal | None
    budget_pro_rata: Decimal | None
    scostamento_valore: Decimal | None
    scostamento_ore: Decimal | None
    tariffa_media_preventivata: Decimal | None
    tariffa_media_consuntivata: Decimal | None
    # An absent estimate is not an estimate of zero: the row says so, is excluded from
    # the budget aggregates, and is never counted as a 100% overrun (§9.2).
    non_preventivato: bool
    # `valore_preventivato` set with `ore_preventivate` null: no progress figure exists
    # to derive a pro-rata from, so only the absolute comparison is shown rather than a
    # progress invented from the invoiced value -- which would be circular, because the
    # invoiced value is the very quantity being judged.
    pro_rata_non_calcolabile: bool


class BudgetPage(BaseModel):
    items: list[BudgetVsActualRow]
    next_cursor: UUID | None
    # Only over rows with both estimate columns populated: including the unestimated ones
    # would make the aggregate depend on how many deals nobody estimated.
    totale_preventivato: Decimal
    totale_ricavi: Decimal
    deal_preventivati: int
    deal_non_preventivati: int


class FiscalEstimate(BaseModel):
    """§8. Declared an **estimate** in its own payload, not only in the UI copy: a
    labelled estimate is useful, an estimate presented as an actual is the original
    defect in a new form."""

    model_config = ConfigDict(frozen=True)

    anno: int
    stima: Literal[True] = True
    avvertenza: str
    ricavi: Decimal
    coefficiente_redditivita: Decimal | None
    imponibile: Decimal | None
    aliquota_imposta_sostitutiva: Decimal | None
    imposta_sostitutiva: Decimal | None
    aliquota_inps: Decimal | None
    contributi: Decimal | None
    reddito_netto_stimato: Decimal | None
    # Substitute tax plus contributions: the one figure a person actually has to set
    # aside. Computed here, once, so no screen adds the two lines up on its own.
    totale_dovuto: Decimal | None = None


class CashMonth(BaseModel):
    """One month of the cash view: what came in, what is still owed, what sits in
    drafts and proformas, what went out. Amounts are `totale` (VAT included: money in
    the bank), costs are `importo`.

    `quote` are the same four amounts as a share of the tallest month of their chart, in
    [0, 1], computed here: the browser scales a bar with them and never turns an amount
    string into a number (apps/web/src/test/no-browser-arithmetic.test.ts). `pila_*` are
    the heights of the two stacked columns as money (ORB-139), summed here for the same
    reason: what the cash chart stacks (`incassato + costi`) and what the projection
    stacks (all four). A column's height, not an income: the costs are inside it, which
    is why the field is not called a total and `proiettato` on `CashOverview` excludes
    them. The chart prints it above a stacked month, where the figure has to match the
    bar a reader measures."""

    anno: int
    mese: int
    incassato: Decimal = Field(max_digits=12, decimal_places=2)
    da_incassare: Decimal = Field(max_digits=12, decimal_places=2)
    bozze: Decimal = Field(max_digits=12, decimal_places=2)
    costi: Decimal = Field(max_digits=12, decimal_places=2)
    pila_andamento: Decimal = Field(
        max_digits=12,
        decimal_places=2,
        description=(
            "Altezza della colonna dell'andamento come importo: incassato + costi passivi. "
            "Non e' un ricavo: i costi sono dentro."
        ),
    )
    pila_proiezione: Decimal = Field(
        max_digits=12,
        decimal_places=2,
        description=(
            "Altezza della colonna della proiezione come importo: incassato + da incassare + "
            "bozze/proforma + costi passivi. Non e' un ricavo: i costi sono dentro."
        ),
    )
    quote_andamento: dict[str, float]
    quote_proiezione: dict[str, float]


class ContractDateMarker(BaseModel):
    """One contract-specific date on the cash calendar (REB-352 §1.6): an
    irrevocability window closing or a renewal deadline, layered onto the month it
    falls in. Neither is a sum and neither moves a `CashMonth` figure -- a marker
    only names a day and which contract it belongs to, the way mastro's own cash
    calendar overlays "irrevocability window ends" and "renewal dates" onto its
    chart without folding either into the chart's own bars
    (`2026-08-23-analysis-section-design.md:205-219`).
    """

    contract_id: UUID
    titolo: str
    customer_id: UUID
    tipo: Literal["fine_irrevocabilita", "scadenza_rinnovo"]
    data: date


class CashOverview(BaseModel):
    """The year as money: `incassato` is invoices paid, `da_incassare` invoices issued
    and unpaid, `bozze` drafts and proformas not yet turned into invoices, `costi` what
    was spent. Which month each document falls in is `base` (`CashBase`): by the
    accrual period it declares, or by the money's own dates. `proiettato` is the first
    three added up -- what the year would collect if everything issued and drafted came
    in -- and the two `lordo` figures are income less costs, actual and projected."""

    anno: int
    # Echoed from the request, so the page can label the reading it shows (ORB-133).
    base: CashBase
    incassato: Decimal = Field(max_digits=12, decimal_places=2)
    da_incassare: Decimal = Field(max_digits=12, decimal_places=2)
    bozze: Decimal = Field(max_digits=12, decimal_places=2)
    proiettato: Decimal = Field(max_digits=12, decimal_places=2)
    costi: Decimal = Field(max_digits=12, decimal_places=2)
    lordo_effettivo: Decimal = Field(max_digits=12, decimal_places=2)
    lordo_proiettato: Decimal = Field(max_digits=12, decimal_places=2)
    mesi: list[CashMonth]
    # REB-352 §1.6's overlay: every irrevocability-window close and renewal
    # deadline that falls inside `anno`, across every contract -- computed "as of"
    # today regardless of which `anno` is on screen (an irrevocability window is
    # always a forward-looking promise), so a marker on a past year's calendar
    # never appears and a marker on a future one only does once today's window
    # actually reaches into it.
    scadenze_contrattuali: list[ContractDateMarker]


class CeilingStatusRead(BaseModel):
    """One ceiling of the configured jurisdiction pack, evaluated against `anno`'s
    real paid revenue -- REB-352 §1.4's headroom figure, the reader-facing shape of
    `fiscal.ceiling.evaluate_ceiling`'s own output. `residuo` (`soglia - ricavi`) is
    the "one number a ceiling exists to produce" mastro's own audit named as
    computed nowhere until REB-361 added `evaluate_ceiling`; this class only
    exposes it, and adds no arithmetic of its own.
    """

    id: str
    etichetta: str
    soglia: Decimal = Field(max_digits=12, decimal_places=2)
    conseguenza: CeilingConsequence
    ricavi: Decimal = Field(max_digits=12, decimal_places=2)
    residuo: Decimal = Field(max_digits=12, decimal_places=2)
    superata: bool
    livello_allerta: str | None


class CeilingHeadroom(BaseModel):
    """Every active ceiling of the fiscal profile's own pack, for one calendar
    year -- `evaluate_pack`'s own list, unmodified."""

    anno: int
    pack_id: str
    pack_version: str
    soglie: list[CeilingStatusRead]


class CeilingSimulationQuery(BaseModel):
    """A not-yet-won deal's own estimate, in the shape its three columns already
    carry (`deals/schemas.py`'s own `DealCreate`) -- accepted raw and never by
    `deal_id`, so REB-352 §1.4's "would this fit?" simulator answers before the
    deal is ever saved. `valore_preventivato` is the synthetic addition directly
    when typed; with only `ore_preventivate` and `tariffa_oraria` set, the service
    derives it as their product, the same two columns `budget_vs_actual` already
    reads plus the one it does not."""

    ore_preventivate: Decimal | None = Field(
        default=None, max_digits=CEILING_ORE_MAX_DIGITS, decimal_places=CEILING_DECIMAL_PLACES, ge=0
    )
    valore_preventivato: Decimal | None = Field(
        default=None,
        max_digits=CEILING_VALORE_MAX_DIGITS,
        decimal_places=CEILING_DECIMAL_PLACES,
        ge=0,
    )
    tariffa_oraria: Decimal | None = Field(
        default=None,
        max_digits=CEILING_FACTOR_MAX_DIGITS,
        decimal_places=CEILING_FACTOR_DECIMAL_PLACES,
        ge=0,
    )


class CeilingSimulationResult(BaseModel):
    """One ceiling, before and after the synthetic addition -- `rientra` is
    "would this fit?" itself: the addition does not push this ceiling's own
    revenue to or past its threshold."""

    id: str
    etichetta: str
    soglia: Decimal = Field(max_digits=12, decimal_places=2)
    conseguenza: CeilingConsequence
    ricavi_attuali: Decimal = Field(max_digits=12, decimal_places=2)
    residuo_attuale: Decimal = Field(max_digits=12, decimal_places=2)
    ricavi_simulati: Decimal = Field(max_digits=12, decimal_places=2)
    residuo_simulato: Decimal = Field(max_digits=12, decimal_places=2)
    rientra: bool
    livello_allerta_simulato: str | None


class CeilingSimulation(BaseModel):
    anno: int
    pack_id: str
    pack_version: str
    aggiunta_sintetica: Decimal = Field(max_digits=12, decimal_places=2)
    soglie: list[CeilingSimulationResult]


class RevenueByCustomer(BaseModel):
    """One customer's share of the year's invoiced revenue -- the concentration figure
    REB-352's own mapping calls for (§1.5, §5 item 1 of
    `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
    `Σ Invoice.imponibile` for this customer over the calendar year, divided by that
    same year's `annual_revenue`.

    Not `EsposizioneCliente` (`dashboard/schemas.py`), whose `quota` is a share of the
    *largest* customer's outstanding receivable and answers "who currently owes the
    most". This answers a different question -- "what share of total invoiced income
    comes from this one client" -- against the whole year's revenue, and the two do not
    become the same figure by relabelling either one.
    """

    customer_id: UUID
    ragione_sociale: str
    ricavi: Decimal = Field(max_digits=12, decimal_places=2)
    fatture: int
    quota: float


class EconomicOverview(BaseModel):
    """Impostazioni economiche della dashboard: la cassa dell'anno e, per un admin con
    un profilo fiscale configurato, la stima fiscale calcolata due volte -- sui ricavi
    incassati e sui ricavi proiettati -- con i netti che ne seguono. Per chi non e'
    admin, o senza profilo, la parte fiscale e' `None` e la pagina mostra solo la cassa."""

    calcolato_alle: datetime
    cassa: CashOverview
    fiscale: FiscalEstimate | None
    fiscale_proiettato: FiscalEstimate | None
    # Whole-practice, calendar-year concentration (§1.5): not blocked on the ledger
    # milestone the way a contract-anchored cap is, and distinct from `per_cliente`'s
    # receivables exposure on the dashboard's own page (`dashboard/schemas.py`).
    concentrazione_clienti: list[RevenueByCustomer]
    netto_effettivo: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    netto_proiettato: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)


class BindTimeRequest(BaseModel):
    """Which hours become invoice lines. `entry_ids` explicit rather than "everything
    billable": choosing *which* hours to invoice is a commercial decision, and a default
    of "all of them" is that decision made silently."""

    entry_ids: list[UUID] = Field(min_length=1)
    raggruppa_per_mese: bool = True


__all__ = [
    "BindTimeRequest",
    "BudgetPage",
    "BudgetQuery",
    "BudgetVsActualRow",
    "DealPnl",
    "DealStato",
    "FiscalEstimate",
    "PeriodPnl",
    "PeriodPnlQuery",
    "PnlTotals",
    "UnbilledBacklog",
]
