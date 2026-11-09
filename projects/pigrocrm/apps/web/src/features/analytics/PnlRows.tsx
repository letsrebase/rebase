import { useId } from 'react'
import { formatHoursValue, formatMoneyValue } from '@/features/time/columns'
import { formatPercent } from './format'
import type { DealPnl, PeriodPnl, PnlBase, PnlTotals } from './queries'

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b py-2 last:border-0">
      <span className="text-muted-foreground">
        {label}
        {hint && <span className="ml-2 text-xs">{hint}</span>}
      </span>
      <span className="text-right font-medium tabular-nums">{value}</span>
    </div>
  )
}

/**
 * The rows of a single deal's conto economico, each one printed exactly as the API sent
 * it. Nothing here adds, subtracts or divides: every figure below was summed by
 * `AnalyticsService` and arrives as a decimal string.
 */
export function PnlRows({ pnl }: { pnl: DealPnl }) {
  // Only `closed` is definitive. `to_invoice` is finished work that has not been
  // billed yet, so its margin is still missing the revenue that is about to arrive --
  // reading "not in corso" as "done" would report a loss on every deal waiting for its
  // invoice.
  const definitive = pnl.status === 'closed'
  return (
    <div>
      <Row
        label="Ricavi fatturati"
        value={formatMoneyValue(pnl.revenue)}
        hint={`${pnl.issued_invoices} fatture emesse`}
      />
      <Row label="Costi diretti" value={formatMoneyValue(pnl.direct_costs)} />
      <Row label="Costo del lavoro" value={formatMoneyValue(pnl.work_cost)} />
      <Row
        label="Margine lordo"
        value={formatMoneyValue(pnl.gross_margin)}
        // The qualifier is never optional on an unfinished deal: a deal with twenty
        // hours and no invoice has a negative margin, and that is not a loss -- it is
        // unfinished work. The report shows a *state* beside the bare number.
        hint={definitive ? '(definitivo)' : '(provvisorio)'}
      />
      <Row label="Margine %" value={formatPercent(pnl.percentage_margin)} />
      {!definitive && (
        <Row
          label="Valore maturato"
          value={formatMoneyValue(pnl.accrued_value)}
          // Dropped once the deal is closed: from then on the revenue is the invoices
          // themselves, and repeating an estimate beside them invites the reader to
          // treat the estimate as a second, disagreeing figure for the same thing.
          hint="stima — non è un ricavo"
        />
      )}
      <Row label="Ore consuntivate" value={formatHoursValue(pnl.total_hours)} />
      <Row label="Ore da fatturare" value={formatHoursValue(pnl.billable_uninvoiced_hours)} />
      {pnl.hours_without_rate > 0 && (
        // A note rather than a row: it is not a figure of the conto economico, it is the
        // reason two of those figures are lower than the work done. An unpriced hour is
        // not a free hour, and it enters neither the accrued value nor the margin.
        <p className="mt-3 text-xs text-muted-foreground">
          {pnl.hours_without_rate} voci senza tariffa: escluse dal valore maturato e dal margine.
        </p>
      )}
    </div>
  )
}

/** What the revenue row says beside its label, so a screenshot of one column still
 *  says which reading it is: the same figure can differ by a month's invoicing between
 *  the two, and a number that does not name its base is a number with no unit. */
const BASE_HINT: Record<PnlBase, string> = {
  issue: 'per emissione',
  accrual: 'per competenza',
}

/**
 * One column of a period P&L. `reportable` marks the closed one and nothing else: the
 * in-progress column is real but provisional, and a reader who takes it for a result
 * has been misled by the layout rather than by any number on it.
 */
function TotalsColumn({
  label,
  totals,
  base,
  reportable = false,
}: {
  label: string
  totals: PnlTotals
  base: PnlBase
  reportable?: boolean
}) {
  const headingId = useId()
  return (
    <div role="group" aria-labelledby={headingId} className="border p-4">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 id={headingId} className="font-semibold">
          {label} ({totals.deal})
        </h3>
        {reportable && (
          <span className="text-xs text-muted-foreground">dato riportabile</span>
        )}
      </div>
      <Row label="Ricavi" value={formatMoneyValue(totals.revenue)} hint={BASE_HINT[base]} />
      <Row label="Costi diretti" value={formatMoneyValue(totals.direct_costs)} />
      {/* `null` is a space with the hours module off: no labour to show, and the margin
          below is the revenue less the direct costs. */}
      {totals.work_cost !== null && (
        <Row label="Costo del lavoro" value={formatMoneyValue(totals.work_cost)} />
      )}
      <Row label="Margine lordo" value={formatMoneyValue(totals.gross_margin)} />
      <Row label="Margine %" value={formatPercent(totals.percentage_margin)} />
    </div>
  )
}

/**
 * The two columns of a period P&L, side by side, with **no combined total** anywhere.
 *
 * That absence is the design: adding a finished job's margin to a half-done one produces
 * a figure that is neither, and one that moves every week for reasons which are not
 * business performance. The backend refuses to return such a field; this component
 * refuses to compute one, which is the only way the refusal survives the trip.
 */
export function PeriodTotals({ pnl, base = 'issue' }: { pnl: PeriodPnl; base?: PnlBase }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <TotalsColumn label="Deal chiusi" totals={pnl.closed} base={base} reportable />
        <TotalsColumn label="Deal in corso" totals={pnl.in_progress} base={base} />
      </div>
      <div>
        <Row label="Spese generali" value={formatMoneyValue(pnl.general_expenses)} />
        <p className="mt-2 text-xs text-muted-foreground">
          Le spese generali non sono ripartite su nessun cliente e su nessun deal: restano
          fuori da entrambe le colonne, invece di essere spalmate con un criterio che
          nessuno ha scelto.
        </p>
      </div>
    </div>
  )
}
