import { useMemo } from 'react'
import type { Deal } from '@/features/deals/queries'
import { formatHoursValue } from './columns'
import type { TimeEntry } from './queries'
import { WeekGridRow } from './WeekGridRow'
import { buildGrid, columnTotal, gridTotal, type WeekDay } from './week'

/**
 * The screen that attacks the real failure mode. §13 argues it explicitly: the way a
 * freelancer's time tracking fails is not "I forgot to stop the timer", it is **"I never
 * entered Tuesday"**. A grid where a whole week is visibly incomplete does something
 * about that, and it stays -- as the «Settimana» tab -- beside the timer that arrived on
 * 2026-09-09.
 *
 * Since that day this is the table alone: the week, the header and the two reads belong
 * to `TimePage`, which hands them down, so the register and the grid never disagree
 * about which week or which rows they are showing.
 */
export function WeekGrid({
  days,
  userId,
  entries,
  deals,
}: {
  days: WeekDay[]
  userId: string
  entries: TimeEntry[]
  deals: Deal[]
}) {
  const grid = useMemo(() => buildGrid(entries, days), [entries, days])
  const dealNames = useMemo(() => new Map(deals.map((deal) => [deal.id, deal.nome])), [deals])
  // Rows: every deal with an entry this week, plus every deal that exists, so a week can
  // be started from nothing rather than only continued. Deduplicated by id; ordered by
  // name through `localeCompare(..., 'it')`, so the row a person is looking for is where
  // the alphabet says it is and does not move when an hour is logged.
  const rows = useMemo(() => {
    const ids = new Set<string>([...grid.keys()])
    for (const deal of deals) ids.add(deal.id)
    return [...ids].sort((left, right) =>
      (dealNames.get(left) ?? '').localeCompare(dealNames.get(right) ?? '', 'it'),
    )
  }, [grid, deals, dealNames])

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto border">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b bg-muted/40">
              <th scope="col" className="p-2 text-left font-medium">
                Deal
              </th>
              {days.map((day) => (
                <th key={day.iso} scope="col" className="p-2 text-center font-medium">
                  {/* The weekday abbreviation alone repeats every week; the day number
                      under it is what tells somebody which week they are looking at
                      without reading back up to the header. */}
                  <span className="block">{day.short}</span>
                  <span className="block text-xs text-muted-foreground">{day.iso.slice(8)}</span>
                </th>
              ))}
              <th scope="col" className="p-2 text-right font-medium">
                Totale
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-4 text-center text-muted-foreground">
                  Nessun deal: creane uno per registrare le ore.
                </td>
              </tr>
            ) : (
              rows.map((dealId) => (
                <WeekGridRow
                  key={dealId}
                  dealId={dealId}
                  dealName={dealNames.get(dealId) ?? dealId}
                  userId={userId}
                  days={days}
                  row={grid.get(dealId)}
                />
              ))
            )}
          </tbody>
          <tfoot>
            <tr className="border-t bg-muted/40 font-medium">
              <th scope="row" className="p-2 text-left">
                Totale
              </th>
              {days.map((day) => (
                <td
                  key={day.iso}
                  data-testid={`column-total-${day.iso}`}
                  className="p-2 text-center tabular-nums"
                >
                  {formatHoursValue(columnTotal(grid, day.iso))}
                </td>
              ))}
              <td data-testid="grid-total" className="p-2 text-right tabular-nums">
                {formatHoursValue(gridTotal(grid))}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <p className="text-xs text-muted-foreground">
        La tariffa viene congelata sulla voce quando la registri. Una cella con più voci
        nello stesso giorno mostra la prima: l&apos;elenco completo è nella tab «Registro» e
        nella tab «Ore» del deal.
      </p>
    </div>
  )
}
