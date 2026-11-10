import type { CSSProperties } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { statusLabel } from '@/features/days/labels'
import { LoadingState } from '@/components/LoadingState'
import {
  ScrollRegion,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { BarRows, BigNumber, type BarRow } from './charts'
import { days, money } from './format'
import type { Period } from './period'
import { useDaysAnalysis, type DaysAnalysis, type DaysMonth, type DaysRate } from './queries'

/**
 * «Giornate»: the days of the period as the contract ledger holds them (REB-756), the
 * port of mastro's `/analysis/days`. Four bands, in the order the card gives them: every
 * day stacked by state, the rate the invoices really earned per day against the rate
 * card, the days that slipped into «lavorato senza approvazione» month by month, and the
 * days recorded against their own trailing average.
 *
 * **Every figure and every bar length comes from the server**: the `share` fields are
 * shares of the tallest month, and the days are the decimal strings the API sent, printed
 * with a decimal comma and nothing else done to them
 * (`src/test/no-browser-arithmetic.test.ts`).
 */

/** The eight states a bar stacks, in the order the server sends them. Only five chart
 *  tokens exist, so from the sixth state on the same hues come back under a diagonal
 *  hatch: a state is told apart by hue and hatch together, and the legend and the table
 *  beside the bar name every one. */
const TONES = ['var(--chart-2)', 'var(--chart-4)', 'var(--chart-3)', 'var(--chart-5)', 'var(--chart-1)']

function toneFor(index: number): CSSProperties {
  const color = TONES[index % TONES.length]!
  return index < TONES.length
    ? { backgroundColor: color }
    : {
        backgroundColor: color,
        backgroundImage:
          'repeating-linear-gradient(45deg, var(--card) 0 2px, transparent 2px 5px)',
      }
}

const monthName = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric' })
const monthShort = new Intl.DateTimeFormat('it-IT', { month: 'short', year: '2-digit' })

/** `2026-03` as «marzo 2026». The month key is a label the server built, and the `Date`
 *  here only names it: no figure is born from it. */
function monthLabel(month: string, short = false): string {
  const date = new Date(`${month}-01T00:00:00`)
  return (short ? monthShort : monthName).format(date)
}

function StackedMonths({ months }: { months: DaysMonth[] }) {
  const states = months[0]?.by_status ?? []
  return (
    <figure className="border bg-card p-4">
      <figcaption className="text-sm font-medium">Giornate per mese e per stato</figcaption>
      <ul className="mt-2 flex flex-wrap gap-4 text-xs text-muted-foreground" aria-label="Legenda">
        {states.map((state, index) => (
          <li key={state.status} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block size-2.5"
              style={toneFor(index)}
            />
            {statusLabel(state.status)}
          </li>
        ))}
      </ul>
      <ul className="mt-4 space-y-1.5" aria-hidden="true">
        {months.map((month) => (
          <li key={month.month} className="flex items-center gap-3 text-sm">
            <span className="w-20 shrink-0 text-muted-foreground">{monthLabel(month.month, true)}</span>
            <div className="flex h-4 min-w-24 flex-1 gap-px bg-muted" data-testid={`stack-${month.month}`}>
              {month.by_status.map((state, index) =>
                state.share > 0 ? (
                  <div
                    key={state.status}
                    data-status={state.status}
                    title={`${statusLabel(state.status)}: ${days(state.days)}`}
                    style={{
                      width: `${(Math.min(1, state.share) * 100).toFixed(2)}%`,
                      ...toneFor(index),
                    }}
                  />
                ) : null,
              )}
            </div>
            <span className="w-12 shrink-0 text-right tabular-nums">{days(month.total)}</span>
          </li>
        ))}
      </ul>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-xs text-muted-foreground">Tabella dei valori</summary>
        <ScrollRegion label="Valori delle giornate per mese e per stato" className="mt-2">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th scope="col" className="py-1 pr-2 font-normal">
                  Mese
                </th>
                {states.map((state) => (
                  <th key={state.status} scope="col" className="py-1 pr-2 font-normal">
                    {statusLabel(state.status)}
                  </th>
                ))}
                <th scope="col" className="py-1 pr-2 font-normal">
                  Totale
                </th>
              </tr>
            </thead>
            <tbody>
              {months.map((month) => (
                <tr key={month.month}>
                  <th scope="row" className="py-1 pr-2 text-left font-normal">
                    {monthLabel(month.month)}
                  </th>
                  {month.by_status.map((state) => (
                    <td key={state.status} className="py-1 pr-2 tabular-nums">
                      {days(state.days)}
                    </td>
                  ))}
                  <td className="py-1 pr-2 tabular-nums">{days(month.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollRegion>
      </details>
    </figure>
  )
}

function RateTable({ rows, first }: { rows: DaysRate[]; first: string }) {
  return (
    <Table scrollLabel={`Tariffa reale per ${first.toLowerCase()}`}>
      <TableHeader>
        <TableRow>
          <TableHead>{first}</TableHead>
          <TableHead className="text-right">Giornate fatturate</TableHead>
          <TableHead className="text-right">Tariffa reale</TableHead>
          <TableHead className="text-right">Tariffa di listino</TableHead>
          <TableHead className="text-right">Senza listino</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.id}>
            <TableCell>{row.label}</TableCell>
            <TableCell className="text-right tabular-nums">{days(row.invoiced_days)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {row.actual_rate ? money(row.actual_rate) : '—'}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {row.nominal_rate ? money(row.nominal_rate) : '—'}
            </TableCell>
            <TableCell className="text-right tabular-nums">{days(row.days_without_list_price)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** The one sentence the trend band exists to say: the last month that is over, against the
 *  three before it. Said of a month that has ended, never of the running one, which would
 *  always read as a decline. */
function trendSentence(analysis: DaysAnalysis): string | null {
  const month = analysis.months.find((m) => m.month === analysis.last_complete_month)
  if (!month) return null
  return `A ${monthLabel(month.month)} hai registrato ${days(month.register)} giornate, contro una media di ${days(month.media_previous)} nei tre mesi prima.`
}

function Days({ analysis }: { analysis: DaysAnalysis }) {
  const { months } = analysis
  const hasDays = months.some((month) => month.total !== '0.00')
  const trendRows: BarRow[] = months.map((month) => ({
    label: monthLabel(month.month, true),
    value: `${days(month.register)} · media ${days(month.media_previous)}`,
    ratio: month.register_share,
    tone: 2,
  }))
  const riskRows: BarRow[] = months.map((month) => ({
    label: monthLabel(month.month, true),
    value: days(month.without_approval),
    ratio: month.share_without_approval,
    tone: 1,
  }))
  const sentence = trendSentence(analysis)

  return (
    <div className="space-y-8">
      <div className="grid gap-4 sm:grid-cols-3">
        <BigNumber
          label="Giornate nel periodo"
          value={days(analysis.days_in_period)}
          tone="accent"
          hint="registrate e in attesa, escluse quelle mai avvenute"
        />
        <BigNumber
          label="Tariffa reale al giorno"
          value={analysis.actual_rate ? money(analysis.actual_rate) : '—'}
          hint={
            analysis.actual_rate
              ? `${days(analysis.invoiced_days)} giornate fatturate`
              : 'nessuna giornata fatturata nel periodo'
          }
        />
        <BigNumber
          label="Senza approvazione"
          value={days(analysis.without_approval_in_period)}
          hint={`giornate finite lì nel periodo · in tutto ${days(analysis.without_approval_always)}`}
        />
      </div>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Giornate per stato</h2>
        {hasDays ? (
          <StackedMonths months={months} />
        ) : (
          <p className="text-sm text-muted-foreground">Nessuna giornata nel periodo.</p>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Tariffa reale al giorno</h2>
        <p className="text-sm text-muted-foreground">
          Quanto hanno reso le righe di fattura emessa sulle giornate che fatturano, contro il listino
          del contratto. Una giornata senza listino in vigore non vale zero: è contata a parte.
        </p>
        {analysis.by_contract.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna giornata fatturata nel periodo.</p>
        ) : (
          <div className="space-y-4">
            <RateTable rows={analysis.by_customer} first="Cliente" />
            <RateTable rows={analysis.by_contract} first="Contratto" />
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Giornate lavorate senza approvazione, mese per mese</h2>
        <p className="text-sm text-muted-foreground">
          Quante giornate sono finite in «lavorato senza approvazione» in ogni mese, anche se poi
          regolarizzate: è la storia, non un allarme sul presente.
        </p>
        {analysis.without_approval_always === '0.00' ? (
          <p className="text-sm text-muted-foreground">
            Nessuna giornata è mai stata lavorata senza l’approvazione che il contratto chiede.
          </p>
        ) : (
          <BarRows caption="Giornate senza approvazione per mese" rows={riskRows} />
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Giornate registrate e media dei tre mesi prima</h2>
        <p className="text-sm text-muted-foreground">
          Le giornate davvero lavorate in ogni mese contro la media dei tre mesi precedenti. Nessuna
          percentuale di capacità: non si sa quante giornate fossero disponibili.
        </p>
        {hasDays ? (
          <>
            {sentence !== null && <p className="text-sm font-semibold">{sentence}</p>}
            <BarRows caption="Giornate registrate per mese" rows={trendRows} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Nessuna giornata nel periodo.</p>
        )}
      </section>
    </div>
  )
}

export function DaysTab({ period }: { period: Period }) {
  const query = useDaysAnalysis(period)
  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) {
    return <LoadingState />
  }
  return <Days analysis={query.data} />
}
