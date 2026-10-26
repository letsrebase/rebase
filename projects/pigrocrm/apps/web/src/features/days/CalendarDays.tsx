import { Link } from '@tanstack/react-router'
import { CalendarPlus } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { useCanWrite } from '@/lib/auth'
import { DayStatePill } from './DayStatePill'
import { HALF_DAY, FULL_DAY, quantitaLabel, statoLabel, statoTone, type CalendarWorkUnit } from './labels'


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
function shortQuantity(quantita: string): string {
  if (quantita === FULL_DAY) return '1 g'
  if (quantita === HALF_DAY) return '½ g'
  return `${quantita.replace(/\.?0+$/, '').replace('.', ',')} g`
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
            data-tone={statoTone(day.stato)}
            className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', TONE_CLASS[statoTone(day.stato)])}
          />
          <span className="min-w-0">
            <span className="tabular-nums">{shortQuantity(day.quantita)}</span>{' '}
            <span className="text-muted-foreground">{day.customer_nome}</span>
            {day.stato === 'lavorato_senza_approvazione' ? (
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
export function PanelDays({ giorno, days }: { giorno: string; days: readonly CalendarWorkUnit[] }) {
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
                aria-label={`${day.contract_titolo}, ${day.customer_nome}, ${quantitaLabel(day.quantita)}, ${statoLabel(day.stato)}`}
              >
                {day.contract_titolo}
                <span className="text-muted-foreground">
                  {' '}
                  · {day.customer_nome} · {quantitaLabel(day.quantita).toLowerCase()}
                </span>
              </Link>
              <DayStatePill stato={day.stato} />
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
