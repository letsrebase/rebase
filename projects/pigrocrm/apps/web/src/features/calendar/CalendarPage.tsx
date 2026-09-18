import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { formatIsoDateItalian } from '@/lib/dates'
import { cn } from '@rebase/ui/cn'
import { DayPanel } from './DayPanel'
import { monthCells, monthLabel, monthOf, shiftMonth, WEEKDAY_LABELS } from './month'
import { useCalendarMonth, type CalendarDay } from './queries'

/**
 * `/app/calendario`: the month, what was worked in it, and what falls due.
 *
 * The page adds no data of its own -- `GET /api/calendario` answers the whole month in
 * one read (slice 10 §6) -- and it draws three things per cell: the hours logged that
 * day, the commitments due, and the invoices due. Only the first two can be *created*
 * from here, and the third is read-only on purpose: an invoice's due date is a property
 * of the invoice, decided when it was issued, and changing it from a view that shows
 * neither the number nor the amount would be changing a fiscal document from the wrong
 * place.
 *
 * The month lives in the URL, so a link to a month is a link. Days with nothing on them
 * are drawn empty by this component rather than sent by the API: a month has thirty-one
 * days and the browser knows it, while thirty-one empty objects per request would be
 * noise.
 */
export function CalendarPage({
  mese,
  onMonthChange,
}: {
  mese: string
  onMonthChange: (mese: string) => void
}) {
  const month = useCalendarMonth(mese)
  const [openDay, setOpenDay] = useState<string | null>(null)

  const cells = useMemo(() => monthCells(mese), [mese])
  const byDay = useMemo(() => {
    const map = new Map<string, CalendarDay>()
    for (const day of month.data?.giorni ?? []) map.set(day.giorno, day)
    return map
  }, [month.data])
  const oggi = month.data?.oggi

  const header = (
    <PageHeader
      icon={CalendarDays}
      title="Calendario"
      description={monthLabel(mese)}
      actions={
        <>
          <Button
            variant="outline"
            size="icon"
            aria-label="Mese precedente"
            onClick={() => onMonthChange(shiftMonth(mese, -1))}
          >
            <ChevronLeft className="size-4" />
          </Button>
          <Button variant="outline" onClick={() => onMonthChange(monthOf(new Date()))}>
            Questo mese
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Mese successivo"
            onClick={() => onMonthChange(shiftMonth(mese, 1))}
          >
            <ChevronRight className="size-4" />
          </Button>
        </>
      }
    />
  )

  // A failed request is neither «loading» nor «nothing happened this month», and a grid
  // drawn empty for it would say the second -- which is exactly the claim this screen
  // exists to make. The banner instead.
  if (month.isError) {
    return (
      <>
        {header}
        <div className="px-8 py-6">
          <QueryErrorBanner error={month.error} />
        </div>
      </>
    )
  }

  const senzaScadenza = month.data?.attivita_senza_scadenza ?? []

  return (
    <>
      {header}
      <div className="space-y-6 px-8 py-6">
        {month.isPending ? (
          <p className="text-sm text-muted-foreground">Caricamento…</p>
        ) : (
          <>
            <div
              className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border bg-border"
              role="grid"
              aria-label={`Calendario di ${monthLabel(mese)}`}
            >
              {WEEKDAY_LABELS.map((label) => (
                <div
                  key={label}
                  className="bg-muted/40 px-2 py-1 text-center text-xs font-medium text-muted-foreground"
                >
                  {label}
                </div>
              ))}
              {cells.map((cell) => (
                <DayCell
                  key={cell.iso}
                  iso={cell.iso}
                  day={cell.day}
                  inMonth={cell.inMonth}
                  isToday={cell.iso === oggi}
                  data={byDay.get(cell.iso)}
                  onOpen={() => setOpenDay(cell.iso)}
                />
              ))}
            </div>

            <p className="text-sm text-muted-foreground">
              Ore del mese: <span className="font-medium text-foreground">{month.data.ore_totali}</span>
            </p>

            {senzaScadenza.length > 0 && (
              <section aria-labelledby="senza-scadenza" className="space-y-2">
                {/* Outside the grid, and that is the point: a commitment with no date
                    cannot be drawn in a cell, is not late and is not for today. A NULL
                    is not zero days. */}
                <h2 id="senza-scadenza" className="text-sm font-medium">
                  Senza scadenza
                </h2>
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {senzaScadenza.map((item) => (
                    <li key={item.id}>{item.titolo}</li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </div>

      {openDay !== null && (
        <DayPanel
          giorno={openDay}
          data={byDay.get(openDay)}
          onClose={() => setOpenDay(null)}
        />
      )}
    </>
  )
}

/**
 * One cell. A button and not a div: the whole cell opens the day, so it has to be
 * reachable from the keyboard and announced as something that can be pressed.
 */
function DayCell({
  iso,
  day,
  inMonth,
  isToday,
  data,
  onOpen,
}: {
  iso: string
  day: number
  inMonth: boolean
  isToday: boolean
  data: CalendarDay | undefined
  onOpen: () => void
}) {
  const attivita = data?.attivita ?? []
  const fatture = data?.fatture ?? []
  const aperte = attivita.filter((item) => item.stato === 'aperta').length
  const chiuse = attivita.length - aperte

  return (
    <button
      type="button"
      onClick={onOpen}
      // The whole date, not the day number: `1` is in the grid twice whenever a month
      // starts or ends mid-week (September 2026 shows 1 September and 1 October), so a
      // label of «1: …» named two cells -- unusable for a screen reader and ambiguous
      // for a test. `aria-current` is what marks today to a reader; the pill marks it
      // to an eye.
      aria-label={`${formatIsoDateItalian(iso)}: ${data ? `${data.ore} ore` : 'niente'}`}
      aria-current={isToday ? 'date' : undefined}
      className={cn(
        'flex min-h-24 flex-col gap-1 bg-background p-2 text-left align-top transition-colors hover:bg-muted/50',
        !inMonth && 'text-muted-foreground/60',
      )}
    >
      <span
        className={cn(
          'text-xs font-medium',
          isToday && 'rounded-full bg-primary px-1.5 py-0.5 text-primary-foreground',
        )}
      >
        {day}
      </span>
      {data && data.ore !== '0.00' && (
        <span className="text-sm font-medium tabular-nums">{data.ore} h</span>
      )}
      {aperte > 0 && (
        <span className="text-xs text-muted-foreground">
          {aperte} {aperte === 1 ? 'scadenza' : 'scadenze'}
        </span>
      )}
      {chiuse > 0 && aperte === 0 && (
        <span className="text-xs text-muted-foreground line-through">{chiuse} fatte</span>
      )}
      {fatture.length > 0 && (
        <span className="text-xs text-muted-foreground">
          {fatture.length === 1 ? '1 fattura' : `${fatture.length} fatture`}
        </span>
      )}
    </button>
  )
}
