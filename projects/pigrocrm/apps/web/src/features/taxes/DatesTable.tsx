import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { NOT_COMPUTABLE } from '@/features/analytics/format'
import { formatIsoDateItalian } from '@/lib/dates'
import { NAMES_ITSELF, dateLabel, distanceLabel, taxOfDate, type DueDay } from './dates'

/** Past this many rows the list says «Mostra altri» (brief, «Many»): a year holds a few dozen at
 *  most. */
export const PAGE_ROWS = 25

/** The lines a day holds. An IVA line already says IVA in its own words; the other taxes name
 *  themselves beside it. A past day is muted, the day itself and what lies ahead are not. */
function Lines({ day, past }: { day: DueDay; past: boolean }) {
  return (
    <div className="space-y-0.5">
      {day.entries.map((entry) => (
        <p key={`${entry.tax}-${entry.kind}`} className={cn('text-sm', past && 'text-muted-foreground')}>
          {dateLabel(entry)}
          {NAMES_ITSELF.has(entry.tax) ? null : (
            <span className="text-xs text-muted-foreground"> · {taxOfDate(entry)}</span>
          )}
        </p>
      ))}
    </div>
  )
}

/**
 * The calendar of one year: a row per day, past ones muted, each with its distance from today
 * («tra 6 giorni»), the lines it holds and its amount. No tax due item carries a net for a date
 * yet, so the amount reads «non calcolabile»; the F24 chip and the state pill come with the due
 * items that would drive them. `compact` is the flat-rate page's section: date and lines only.
 */
export function DatesTable({
  days,
  today,
  selected,
  wide,
  compact = false,
  onSelect,
}: {
  days: readonly DueDay[]
  today: string
  selected: string | null
  wide: boolean
  compact?: boolean
  onSelect: (date: string) => void
}) {
  const [shown, setShown] = useState(PAGE_ROWS)
  const rows = days.slice(0, shown)
  const more = days.length > rows.length
  return (
    <div className="space-y-3">
      {wide ? (
        <div className="overflow-x-auto overflow-y-hidden border bg-card">
          <Table scrollLabel="Scadenze fiscali">
            <TableHeader>
              <TableRow>
                <TableHead>Scadenza</TableHead>
                <TableHead>Cosa</TableHead>
                {compact ? null : <TableHead className="text-right">Importo</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((day) => (
                <TableRow
                  key={day.date}
                  data-state={day.date === selected ? 'selected' : undefined}
                  aria-selected={day.date === selected}
                  tabIndex={0}
                  className={cn(
                    'cursor-pointer align-top focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                    day.date < today && 'text-muted-foreground',
                  )}
                  onClick={() => onSelect(day.date)}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return
                    event.preventDefault()
                    onSelect(day.date)
                  }}
                >
                  <TableCell className="whitespace-nowrap">
                    <p className={cn('font-medium', day.date < today && 'text-muted-foreground')}>
                      {formatIsoDateItalian(day.date)}
                    </p>
                    <p className="text-xs text-muted-foreground">{distanceLabel(today, day.date)}</p>
                  </TableCell>
                  <TableCell>
                    <Lines day={day} past={day.date < today} />
                  </TableCell>
                  {compact ? null : (
                    <TableCell className="whitespace-nowrap text-right tabular-nums text-muted-foreground">
                      {NOT_COMPUTABLE}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Scadenze fiscali">
          {rows.map((day) => (
            <li key={day.date}>
              <button
                type="button"
                onClick={() => onSelect(day.date)}
                className={cn(
                  'block w-full space-y-1 border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  day.date < today && 'text-muted-foreground',
                )}
              >
                <p className={cn('font-medium', day.date < today && 'text-muted-foreground')}>
                  {formatIsoDateItalian(day.date)}{' '}
                  <span className="text-xs font-normal text-muted-foreground">
                    · {distanceLabel(today, day.date)}
                  </span>
                </p>
                <Lines day={day} past={day.date < today} />
                {compact ? null : (
                  <p className="text-sm text-muted-foreground tabular-nums">{NOT_COMPUTABLE}</p>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {more ? (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => setShown(shown + PAGE_ROWS)}>
            Mostra altri
          </Button>
        </div>
      ) : null}
    </div>
  )
}
