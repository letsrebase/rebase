import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { StatusPill } from '@/components/StatusPill'
import { money } from '@/features/dashboard/format'
import { moneyIn } from '@/features/cash/money'
import { formatIsoDateItalian } from '@/lib/dates'
import {
  CARRY_LABEL,
  CARRY_TONE,
  F24_CHIP_LABEL,
  F24_CHIP_TONE,
  f24Chip,
  TAX_ITEM_STATE_LABEL,
  TAX_ITEM_STATE_TONE,
  itemTitle,
  taxItemState,
} from './items'
import { NAMES_ITSELF, dateLabel, distanceLabel, taxOfDate, type DueDay } from './dates'

/** Past this many rows the list says «Mostra altri» (brief, «Many»): a year holds a few dozen at
 *  most. */
export const PAGE_ROWS = 25

/** The lines a day holds. The pack's own lines name the date and the tax beside it (an IVA
 *  line already says IVA); each due item (a balance, an advance, an instalment, the chamber fee)
 *  names itself, with its amount and its state as the server gives them. A past day is muted, the
 *  day itself and what lies ahead are not. */
function Lines({ day, today, compact }: { day: DueDay; today: string; compact: boolean }) {
  const past = day.date < today
  const chip = f24Chip(day)
  const group = day.groups.find((candidate) => candidate.state !== 'dissolved')
  return (
    <div className="space-y-1.5">
      {day.items.map((item) => {
        const state = taxItemState(item, today)
        return (
          <div key={item.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <p className={cn('text-sm', past && state !== 'overdue' && 'text-muted-foreground')}>
              {itemTitle(item)}
            </p>
            <span className="flex items-center gap-2">
              {compact ? null : (
                <span className="text-sm tabular-nums">{moneyIn(item.amount, item.currency)}</span>
              )}
              <StatusPill tone={TAX_ITEM_STATE_TONE[state]}>{TAX_ITEM_STATE_LABEL[state]}</StatusPill>
              {item.carry_to_confirm ? <StatusPill tone={CARRY_TONE}>{CARRY_LABEL}</StatusPill> : null}
            </span>
          </div>
        )
      })}
      {chip ? (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <StatusPill tone={F24_CHIP_TONE[chip]}>{F24_CHIP_LABEL[chip]}</StatusPill>
          {group && !compact ? (
            <span className="text-sm tabular-nums">Netto {money(group.total)}</span>
          ) : null}
        </div>
      ) : null}
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
 * («tra 6 giorni») and the lines it holds, each due item with its own amount and state pill and a
 * «Da confermare» mark when a carried difference waits. A date's net is the server's and exists
 * only with an F24 group, so no sum is drawn here. `compact` is the flat-rate page's section: date
 * and lines only.
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
                <TableHead>Cosa e quanto</TableHead>
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
                    <Lines day={day} today={today} compact={compact} />
                  </TableCell>
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
                <Lines day={day} today={today} compact={compact} />
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
