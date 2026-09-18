import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Input } from '@rebase/ui/input'
import { toProblem } from '@/lib/api'
import { formatHoursValue } from './columns'
import { useDeleteTimeEntry, useLogTime, useUpdateHours } from './queries'
import { hoursForInput, normaliseHours, rowTotal, type GridCell, type WeekDay } from './week'

// A module constant, not `new Map()` inline: a fresh empty Map on every render would be
// a new `cells` identity each time, for a row that has not changed at all.
const EMPTY_ROW = new Map<string, GridCell>()

export interface WeekGridRowProps {
  dealId: string
  dealName: string
  /** The logged-in user, whose week this is. `TimeEntryCreate` requires `user_id`, and
   *  it comes from the session rather than from a control for the same reason `deal_id`
   *  comes from the row: attributing billable hours to the wrong person is not something
   *  a stray click should be able to do. */
  userId: string
  days: WeekDay[]
  row: Map<string, GridCell> | undefined
}

/**
 * One deal's week: seven editable cells and a total.
 *
 * Every cell is uncontrolled-until-touched -- `draft` holds only the days the user has
 * actually typed into, so a refetch landing mid-week updates every other cell without
 * yanking the one under the cursor. The write happens on blur, which is what makes
 * tabbing across a week feel like a spreadsheet rather than like seven forms.
 */
export function WeekGridRow({ dealId, dealName, userId, days, row }: WeekGridRowProps) {
  const cells = row ?? EMPTY_ROW
  const [draft, setDraft] = useState<Record<string, string>>({})
  const log = useLogTime()
  const updateHours = useUpdateHours()
  const remove = useDeleteTimeEntry()

  const failWith = (error: unknown) => toast.error(toProblem(error).detail)

  function commit(day: WeekDay, cell: GridCell | undefined) {
    const raw = draft[day.iso]
    // Untouched: blurring a cell nobody typed into must not write anything. Without
    // this, tabbing across a week would re-POST every value it passed over.
    if (raw === undefined) return
    setDraft((current) => {
      const next = { ...current }
      delete next[day.iso]
      return next
    })

    const value = normaliseHours(raw)
    // Compared against the stored decimal string, never against a parsed number: `2.50`
    // and `2.5` are the same quantity but not the same value, and the server is the one
    // that decides which of them it stores.
    if (value === (cell?.ore ?? '')) return

    if (value === '') {
      // Cleared: the honest action is a reversible soft delete, not an entry of zero
      // hours -- `ore > 0` is the rule, and a zero-hour row is not a row (§2.2).
      if (cell?.entryId) {
        remove.mutate({ entryId: cell.entryId, dealId }, { onError: failWith })
      }
      return
    }

    if (cell?.entryId) {
      updateHours.mutate({ entryId: cell.entryId, ore: value }, { onError: failWith })
      return
    }

    log.mutate(
      {
        deal_id: dealId,
        user_id: userId,
        data: day.iso,
        ore: value,
        // A grid cell has no description field: typing a number is the whole point of
        // the screen, and a required text box in each of thirty-five cells would be the
        // friction this grid exists to remove. The default says honestly where the entry
        // came from, and the deal's Ore tab -- which has room -- is where it is replaced.
        descrizione: 'Ore registrate dalla griglia settimanale',
      },
      { onError: failWith },
    )
  }

  return (
    <tr className="border-b last:border-0">
      <th scope="row" className="p-2 text-left font-normal">
        {dealName}
      </th>
      {days.map((day) => {
        const cell = cells.get(day.iso)
        return (
          <td key={day.iso} className="p-1 text-center">
            <Input
              // Deal *and* day: "2,5" on its own tells a screen reader nothing about
              // which of thirty-five identical boxes it is reading.
              aria-label={`${dealName}, ${day.label}`}
              inputMode="decimal"
              className="h-9 w-16 text-center tabular-nums"
              value={draft[day.iso] ?? hoursForInput(cell?.ore ?? null)}
              onChange={(event) =>
                setDraft((current) => ({ ...current, [day.iso]: event.target.value }))
              }
              onBlur={() => commit(day, cell)}
              onKeyDown={(event) => {
                // Enter commits by blurring rather than by calling `commit` directly, so
                // there is exactly one path to a write and it cannot fire twice.
                if (event.key === 'Enter') event.currentTarget.blur()
              }}
            />
          </td>
        )
      })}
      <td data-testid={`row-total-${dealId}`} className="p-2 text-right tabular-nums">
        {formatHoursValue(rowTotal(cells))}
      </td>
    </tr>
  )
}
