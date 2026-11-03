import { Link } from '@tanstack/react-router'
import { CalendarPlus } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { useCanWrite } from '@/lib/auth'
import { formatItalianQuantity } from '@/lib/decimal'
import { DayStatePill } from './DayStatePill'
import { HALF_DAY, FULL_DAY, quantityLabel, statusLabel, statusTone, type CalendarWorkUnit } from './labels'


// The same tints `Badge`'s dot carries, by name (`shared/ui/badge.tsx`, `BADGE_DOTS`):
// a state reads the same colour on a cell as on its pill, and no tint is invented here.
const TONE_CLASS = {
  ink: 'bg-foreground',
  muted: 'bg-muted-foreground',
  gold: 'bg-royal-gold',
  danger: 'bg-watermelon',
  accent: 'bg-accent',
} as const

/** `1 g` and `½ g` on a cell, which has no room for «Mezza giornata»; the panel spells it out. */
function shortQuantity(quantity: string): string {
  if (quantity === FULL_DAY) return '1 g'
  if (quantity === HALF_DAY) return '½ g'
  return `${formatItalianQuantity(quantity)} g`
}

/**
 * The contract days of one calendar cell (REB-748): a dot in the state's tone, how much of
 * a day, and the customer, one line each. A day worked without the approval its contract
 * requires says so in words on the cell, because a dot alone is a colour a person has to
 * remember the meaning of, and that is the one state the page exists to make visible.
 */
export function CellDays({ days }: { days: readonly CalendarWorkUnit[] }) {
  if (days.length === 0) return null
  return (
    // Spans, not a list: a cell is a `<button>`, whose content may only be phrasing.
    <span className="block space-y-0.5">
      {days.map((day) => (
        <span key={day.id} className="flex items-start gap-1.5 text-xs">
          <span
            aria-hidden="true"
            data-tone={statusTone(day.status)}
            className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', TONE_CLASS[statusTone(day.status)])}
          />
          <span className="min-w-0">
            <span className="tabular-nums">{shortQuantity(day.quantity)}</span>{' '}
            <span className="text-muted-foreground">{day.customer_name}</span>
            {day.status === 'worked_without_approval' ? (
              <span className="block font-medium">senza approvazione</span>
            ) : null}
          </span>
        </span>
      ))}
    </span>
  )
}

/**
 * The days section of the day panel: every contract day of the date as a link to its page,
 * with its state, and the way to record one for this date. Recording is offered whether or
 * not the date has a day: a date can hold half a day and a person may want to see it
 * before adding to it, and the form itself says when the date is taken.
 */
export function PanelDays({ day: giorno, days }: { day: string; days: readonly CalendarWorkUnit[] }) {
  const canWrite = useCanWrite()
  return (
    <section aria-labelledby="giornate-del-giorno" className="space-y-2">
      <h3 id="giornate-del-giorno" className="text-sm font-medium">
        Giornate
      </h3>
      {days.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessuna giornata di contratto.</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {days.map((day) => (
            <li key={day.id} className="flex flex-wrap items-center justify-between gap-2">
              <Link
                to="/app/days/$workUnitId"
                params={{ workUnitId: day.id }}
                className="min-w-0 underline underline-offset-4"
                aria-label={`${day.contract_title}, ${day.customer_name}, ${quantityLabel(day.quantity)}, ${statusLabel(day.status)}`}
              >
                {day.contract_title}
                <span className="text-muted-foreground">
                  {' '}
                  · {day.customer_name} · {quantityLabel(day.quantity).toLowerCase()}
                </span>
              </Link>
              <DayStatePill status={day.status} />
            </li>
          ))}
        </ul>
      )}
      {canWrite ? (
        <Button asChild variant="outline" size="sm">
          <Link to="/app/days/new" search={{ data: giorno }}>
            <CalendarPlus className="mr-2 size-4" aria-hidden />
            Registra una giornata
          </Link>
        </Button>
      ) : null}
    </section>
  )
}
