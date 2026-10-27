import { CalendarDays, ChevronLeft, ChevronRight, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { formatIsoDateItalian } from '@/lib/dates'
import { formatItalianQuantity } from '@/lib/decimal'
import { useCanWrite } from '@/lib/auth'
import { cn } from '@rebase/ui/cn'
import { AttivitaDialog } from '@/features/attivita/AttivitaDialog'
import { AttivitaItem } from '@/features/attivita/AttivitaItem'
import { CellDays } from '@/features/days/CalendarDays'
import { cellDaysLabel } from '@/features/days/labels'
import { DayPanel } from './DayPanel'
import { monthCells, monthLabel, monthOf, shiftMonth, WEEKDAY_LABELS, type MonthCell } from './month'
import { useCalendarMonth, type CalendarDay } from './queries'

/**
 * `/app/calendar`: the month, what was worked in it, and what falls due.
 *
 * The page adds no data of its own -- `GET /api/calendar` answers the whole month in
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
  const [creating, setCreating] = useState(false)
  const canWrite = useCanWrite()

  const weeks = useMemo(() => {
    const cells = monthCells(mese)
    const rows: MonthCell[][] = []
    for (let start = 0; start < cells.length; start += 7) rows.push(cells.slice(start, start + 7))
    return rows
  }, [mese])
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
          {canWrite && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="mr-2 size-4" />
              Nuova attività
            </Button>
          )}
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
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader className="size-4" />
            Caricamento…
          </p>
        ) : (
          <>
            {/* One column of days below `md` and the seven-column month from there: seven
                columns on a 318px page leave a cell 36px wide, which is what made «1 g Acme
                Srl» wrap one letter per line. The structure is the ARIA grid pattern
                (grid > row > columnheader / gridcell > button): a bare `button` child of a
                grid is what axe refuses (`aria-required-children`), and `display: contents`
                on a row lets the CSS grid keep laying out the cells. */}
            <div
              className="grid grid-cols-1 gap-px overflow-hidden border bg-border md:grid-cols-7"
              role="grid"
              aria-label={`Calendario di ${monthLabel(mese)}`}
            >
              <div role="row" className="contents max-md:hidden">
                {WEEKDAY_LABELS.map((label) => (
                  <div
                    key={label}
                    role="columnheader"
                    className="bg-muted px-2 py-1 text-center text-xs font-medium text-muted-foreground"
                  >
                    {label}
                  </div>
                ))}
              </div>
              {weeks.map((week) => (
                <div key={week[0]?.iso} role="row" className="contents">
                  {week.map((cell, column) => (
                    <DayCell
                      key={cell.iso}
                      iso={cell.iso}
                      day={cell.day}
                      weekday={WEEKDAY_LABELS[column] ?? ''}
                      inMonth={cell.inMonth}
                      isToday={cell.iso === oggi}
                      data={byDay.get(cell.iso)}
                      onOpen={() => setOpenDay(cell.iso)}
                    />
                  ))}
                </div>
              ))}
            </div>

            <p className="text-sm text-muted-foreground">
              Ore del mese:{' '}
              <span className="font-medium text-foreground">
                {formatItalianQuantity(month.data.ore_totali)}
              </span>
            </p>

            {senzaScadenza.length > 0 && (
              <section aria-labelledby="senza-scadenza" className="space-y-2">
                {/* Outside the grid, and that is the point: a commitment with no date
                    cannot be drawn in a cell, is not late and is not for today. A NULL
                    is not zero days. */}
                <h2 id="senza-scadenza" className="text-sm font-medium">
                  Senza scadenza
                </h2>
                <ul className="space-y-2 text-sm">
                  {senzaScadenza.map((item) => (
                    <AttivitaItem key={item.id} attivita={item} />
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
      <AttivitaDialog open={creating} onOpenChange={setCreating} />
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
  weekday,
  inMonth,
  isToday,
  data,
  onOpen,
}: {
  iso: string
  day: number
  weekday: string
  inMonth: boolean
  isToday: boolean
  data: CalendarDay | undefined
  onOpen: () => void
}) {
  const attivita = data?.attivita ?? []
  const fatture = data?.fatture ?? []
  const giornate = data?.giornate ?? []
  const aperte = attivita.filter((item) => item.stato === 'aperta').length
  const chiuse = attivita.length - aperte
  const ore = data ? formatItalianQuantity(data.ore) : null

  return (
    // The cell is the `gridcell`; the button inside it is what opens the day. On a phone
    // the neighbouring months' days are not drawn at all: a list of one month's days with
    // the previous month's tail on top is a list with the wrong first row.
    <div
      role="gridcell"
      className={cn('bg-background', inMonth ? undefined : 'bg-muted max-md:hidden')}
    >
      <button
        type="button"
        onClick={onOpen}
        // The whole date, not the day number: `1` is in the grid twice whenever a month
        // starts or ends mid-week (September 2026 shows 1 September and 1 October), so a
        // label of «1: …» named two cells -- unusable for a screen reader and ambiguous
        // for a test. `aria-current` is what marks today to a reader; the pill marks it
        // to an eye.
        aria-label={`${formatIsoDateItalian(iso)}: ${ore !== null ? `${ore} ore` : 'niente'}${cellDaysLabel(giornate)}`}
        aria-current={isToday ? 'date' : undefined}
        className={cn(
          'flex size-full min-h-12 flex-row items-start gap-3 p-2 text-left align-top transition-colors hover:bg-muted/50',
          'md:min-h-24 md:flex-col md:gap-1',
          !inMonth && 'text-muted-foreground',
        )}
      >
        <span className="flex w-14 shrink-0 items-baseline gap-1.5 text-xs font-medium md:w-auto">
          {/* Below `md` the cell is a row of a list, which has no column header above it
              to say which weekday it is. */}
          <span className="uppercase tracking-wide text-muted-foreground md:hidden">{weekday}</span>
          <span className={cn(isToday && 'bg-primary px-1.5 py-0.5 text-primary-foreground')}>
            {day}
          </span>
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          {ore !== null && data !== undefined && data.ore !== '0.00' && (
            <span className="text-sm font-medium tabular-nums">{ore} h</span>
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
          <CellDays days={giornate} />
        </span>
      </button>
    </div>
  )
}
