import type { ReactNode } from 'react'
import { cn } from '@rebase/ui/cn'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { money } from '@/features/dashboard/format'
import { Balance, Flow } from './Flow'
import { itemsCount, weekLabel } from './labels'
import type { CashSchedule, CashWeek } from './queries'

/** The words that say a week's dates follow another item (design § 1.3), once per row. */
const ESTIMATED_NOTE = 'con date circa'

function WeekName({ week }: { week: CashWeek }) {
  return (
    <span className="flex flex-col">
      <span className="font-medium">{weekLabel(week)}</span>
      <span className="text-xs text-muted-foreground">
        {week.is_current ? 'Questa settimana · ' : ''}
        {itemsCount(week.item_count)}
        {week.has_estimated_date ? `, ${ESTIMATED_NOTE}` : ''}
      </span>
    </span>
  )
}

/**
 * The weeks as a table (decision 1): the server's rows in the server's order, one balance per
 * week with the forecasts in (decision 2), and no «Saldo certo» column. «Uscite» and «Netto»
 * exist only when the schedule has an outgoing side, «Saldo a fine settimana» only when it has
 * a balance: a column that would be empty is not drawn.
 */
export function WeeksTable({
  schedule,
  selected,
  onSelect,
}: {
  schedule: CashSchedule
  selected: string | null
  onSelect: (start: string) => void
}) {
  return (
    <Table scrollLabel="Settimane" aria-label="Le settimane">
      <TableHeader>
        <TableRow>
          <TableHead>Settimana</TableHead>
          <TableHead className="text-right">Entrate</TableHead>
          {schedule.show_outgoing && <TableHead className="text-right">Uscite</TableHead>}
          {schedule.show_outgoing && <TableHead className="text-right">Netto</TableHead>}
          {schedule.show_balance && <TableHead className="text-right">Saldo a fine settimana</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {schedule.weeks.map((week) => {
          const open = selected === week.start
          return (
            <TableRow
              key={week.start}
              data-state={open ? 'selected' : undefined}
              aria-current={week.is_current ? 'date' : undefined}
              className={cn('cursor-pointer', open && 'bg-muted')}
              onClick={() => onSelect(week.start)}
            >
              <TableCell>
                <button
                  type="button"
                  aria-pressed={open}
                  aria-label={`Settimana dal ${weekLabel(week)}`}
                  className="text-left outline-none focus-visible:underline"
                  onClick={(event) => {
                    event.stopPropagation()
                    onSelect(week.start)
                  }}
                >
                  <WeekName week={week} />
                </button>
              </TableCell>
              <TableCell className="text-right align-top">
                <Flow expected={week.in_expected} forecast={week.in_forecast} />
              </TableCell>
              {schedule.show_outgoing && (
                <TableCell className="text-right align-top">
                  {week.out_expected !== null && week.out_forecast !== null && (
                    <Flow expected={week.out_expected} forecast={week.out_forecast} />
                  )}
                </TableCell>
              )}
              {schedule.show_outgoing && (
                <TableCell className="text-right align-top tabular-nums">
                  {week.net !== null && money(week.net)}
                </TableCell>
              )}
              {schedule.show_balance && (
                <TableCell className="text-right align-top">
                  {week.balance_end !== null && <Balance value={week.balance_end} belowZero={week.below_zero} />}
                </TableCell>
              )}
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  )
}

/** One card per week on the phone (decision 1): the same figures as the table's row, each on
 *  its own line, and a visible button-sized card that opens the week full screen. */
export function WeekCards({
  schedule,
  onSelect,
}: {
  schedule: CashSchedule
  onSelect: (start: string) => void
}) {
  return (
    <ul aria-label="Le settimane" className="space-y-3">
      {schedule.weeks.map((week) => (
        <li key={week.start}>
          <button
            type="button"
            aria-label={`Settimana dal ${weekLabel(week)}`}
            aria-current={week.is_current ? 'date' : undefined}
            onClick={() => onSelect(week.start)}
            className="block w-full min-w-0 border border-border bg-card p-4 text-left"
          >
            <WeekName week={week} />
            <dl className="mt-3 space-y-2">
              <Line label="Entrate">
                <Flow expected={week.in_expected} forecast={week.in_forecast} />
              </Line>
              {schedule.show_outgoing && week.out_expected !== null && week.out_forecast !== null && (
                <Line label="Uscite">
                  <Flow expected={week.out_expected} forecast={week.out_forecast} />
                </Line>
              )}
              {schedule.show_balance && week.balance_end !== null && (
                <Line label="Saldo a fine settimana">
                  <Balance value={week.balance_end} belowZero={week.below_zero} />
                </Line>
              )}
            </dl>
          </button>
        </li>
      ))}
    </ul>
  )
}
