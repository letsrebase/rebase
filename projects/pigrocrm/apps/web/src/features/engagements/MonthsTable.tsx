import type { UseQueryResult } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toProblem } from '@/lib/api'
import { cn } from '@rebase/ui/cn'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import {
  AmountCell,
  AmountText,
  PaymentCell,
  PaymentItem,
  QuantityCell,
  ReportCell,
} from './MonthParts'
import { HIDDEN, unitHeading } from './words'
import {
  monthLabel,
  type DaysReport,
  type Engagement,
  type EngagementPaymentPage,
  type EngagementPayment,
} from './labels'
import type { MonthPlan, MonthRow } from './monthRows'

const TABLE = '(min-width: 768px)'

/** The report refused outside the space `PIGROCRM_LEADS_SPACE` names: a conflict, not a failure. */
const CONFLICT = 409

/** «Da dicembre 2026 a febbraio 2027: 3 mesi previsti», the forecasts after the last month read. */
function futureWords(plan: MonthPlan): string | null {
  const future = plan.future
  if (future === null) return null
  const count = `${future.months} ${future.months === 1 ? 'mese previsto' : 'mesi previsti'}`
  return future.firstMonth === future.lastMonth
    ? `${monthLabel(future.firstMonth)}: ${count}`
    : `Da ${monthLabel(future.firstMonth)} a ${monthLabel(future.lastMonth)}: ${count}`
}

function OtherPayments({
  payments,
  redacted,
  today,
}: {
  payments: readonly EngagementPayment[]
  redacted: boolean
  today: string
}) {
  if (payments.length === 0) return null
  return (
    <section aria-label="Altri pagamenti" className="space-y-2">
      <h3 className="text-sm font-medium">Altri pagamenti</h3>
      <ul className="divide-y border bg-card text-sm">
        {payments.map((payment) => (
          <li key={payment.due_item_id} className="flex items-start justify-between gap-4 px-3 py-2">
            <PaymentItem payment={payment} today={today} />
            {redacted ? (
              <span className="text-muted-foreground">{HIDDEN}</span>
            ) : (
              <AmountText payment={payment} />
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function MonthName({ row }: { row: MonthRow }) {
  return <span className="first-letter:uppercase">{monthLabel(row.month)}</span>
}

function DesktopTable({
  plan,
  report,
  engagement,
  selected,
  onSelect,
  today,
}: {
  plan: MonthPlan
  report: DaysReport | undefined
  engagement: Engagement
  selected: string | null
  onSelect: (key: string) => void
  today: string
}) {
  const available = report?.available === true
  const flat = engagement.fee_unit === 'flat'
  const future = futureWords(plan)
  return (
    <div className="border bg-card">
      <Table scrollLabel="I mesi">
        <TableHeader>
          <TableRow>
            <TableHead>Mese</TableHead>
            <TableHead className="text-right">{unitHeading(report)}</TableHead>
            <TableHead>Rendiconto</TableHead>
            <TableHead className="text-right">Importo</TableHead>
            <TableHead>Pagamento al freelance</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {plan.rows.map((row) => (
            <TableRow
              key={row.key}
              data-state={selected === row.key ? 'selected' : undefined}
              className="cursor-pointer align-top"
              onClick={() => onSelect(row.key)}
            >
              <TableCell className="px-3 py-3 align-top font-medium">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-left underline-offset-4 hover:underline"
                  aria-current={selected === row.key ? 'true' : undefined}
                  aria-label={`Apri ${monthLabel(row.month)}`}
                  onClick={(event) => {
                    event.stopPropagation()
                    onSelect(row.key)
                  }}
                >
                  <MonthName row={row} />
                </button>
              </TableCell>
              <TableCell className="px-3 py-3 align-top">
                <QuantityCell row={row} flat={flat} available={available} />
              </TableCell>
              <TableCell className="px-3 py-3 align-top">
                <ReportCell row={row} />
              </TableCell>
              <TableCell className="px-3 py-3 align-top">
                <AmountCell row={row} redacted={engagement.redacted} />
              </TableCell>
              <TableCell className="px-3 py-3 align-top">
                <PaymentCell row={row} today={today} />
              </TableCell>
            </TableRow>
          ))}
          {future === null ? null : (
            <TableRow>
              <TableCell colSpan={5} className="px-3 py-3 text-muted-foreground">
                {future}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}

/** The phone's month: one card with the same data, no table. */
function MonthCards({
  plan,
  report,
  engagement,
  selected,
  onSelect,
  today,
}: {
  plan: MonthPlan
  report: DaysReport | undefined
  engagement: Engagement
  selected: string | null
  onSelect: (key: string) => void
  today: string
}) {
  const available = report?.available === true
  const flat = engagement.fee_unit === 'flat'
  const future = futureWords(plan)
  return (
    <ul className="space-y-3">
      {plan.rows.map((row) => (
        <li
          key={row.key}
          data-state={selected === row.key ? 'selected' : undefined}
          className={cn('border bg-card', selected === row.key && 'bg-secondary')}
        >
          <button
            type="button"
            aria-label={`Apri ${monthLabel(row.month)}`}
            className="flex w-full items-center justify-between gap-2 border-b px-4 py-3 text-left font-medium"
            onClick={() => onSelect(row.key)}
          >
            <MonthName row={row} />
            <ChevronRight aria-hidden="true" className="size-4 text-muted-foreground" />
          </button>
          <dl className="grid grid-cols-[minmax(0,6rem)_minmax(0,1fr)] gap-x-3 gap-y-3 px-4 py-3 text-sm">
            <dt className="text-muted-foreground">{unitHeading(report)}</dt>
            <dd className="text-left [&_div]:text-left">
              <QuantityCell row={row} flat={flat} available={available} />
            </dd>
            <dt className="text-muted-foreground">Rendiconto</dt>
            <dd>
              <ReportCell row={row} />
            </dd>
            <dt className="text-muted-foreground">Importo</dt>
            <dd className="[&_span]:text-left [&>div]:text-left">
              <AmountCell row={row} redacted={engagement.redacted} />
            </dd>
            <dt className="text-muted-foreground">Pagamento</dt>
            <dd>
              <PaymentCell row={row} today={today} />
            </dd>
          </dl>
        </li>
      ))}
      {future === null ? null : <li className="px-1 text-sm text-muted-foreground">{future}</li>}
    </ul>
  )
}

/**
 * The table «I mesi» (decision 2A of the brief): one row per month the freelancer's days are read for,
 * then a summary row for the forecasts after them, with its own loading and error states so a slow or
 * failed read of the days leaves the letter and its actions where they are. A card per month on a phone.
 * A report that is not available (the freelancer's space is not set up, or the server refuses outside
 * its own space with a conflict) leaves «-» under «Giorni» and one line with what the server said.
 */
export function MonthsTable({
  engagement,
  report,
  payments,
  plan,
  selected,
  onSelect,
  today,
}: {
  engagement: Engagement
  report: UseQueryResult<DaysReport, unknown>
  payments: UseQueryResult<EngagementPaymentPage, unknown>
  /** The report's months joined with the payments (`planMonths`), computed once by the page. */
  plan: MonthPlan
  selected: string | null
  onSelect: (key: string) => void
  today: string
}) {
  const wide = useMediaQuery(TABLE)
  const started = engagement.state !== 'draft' && engagement.state !== 'awaiting_signature'
  const refused = report.isError && toProblem(report.error).status === CONFLICT

  let body
  if (!started) {
    body = <p className="text-sm text-muted-foreground">I mesi compaiono quando l'incarico parte.</p>
  } else if (report.isPending || payments.isPending) {
    body = <LoadingState label="Leggo i giorni…" />
  } else if (report.isError && !refused) {
    body = <QueryErrorBanner error={report.error} onRetry={() => void report.refetch()} />
  } else {
    const note = refused ? toProblem(report.error).detail : report.data?.available === false ? report.data.note : null
    body = (
      <div className="space-y-4">
        {payments.isError ? (
          <QueryErrorBanner error={payments.error} onRetry={() => void payments.refetch()} />
        ) : null}
        {note === null || note === undefined ? null : (
          <p role="note" className="border bg-paper px-3 py-2 text-sm">
            {note}
          </p>
        )}
        {plan.rows.length === 0 && plan.future === null ? (
          <p className="text-sm text-muted-foreground">Nessun mese da mostrare, per ora.</p>
        ) : wide ? (
          <DesktopTable
            plan={plan}
            report={refused ? undefined : report.data}
            engagement={engagement}
            selected={selected}
            onSelect={onSelect}
            today={today}
          />
        ) : (
          <MonthCards
            plan={plan}
            report={refused ? undefined : report.data}
            engagement={engagement}
            selected={selected}
            onSelect={onSelect}
            today={today}
          />
        )}
        <OtherPayments payments={plan.others} redacted={engagement.redacted} today={today} />
      </div>
    )
  }

  return (
    <section aria-labelledby="engagement-months" className="space-y-3">
      <h2 id="engagement-months" className="text-lg font-semibold">
        I mesi
      </h2>
      {body}
    </section>
  )
}
