/**
 * The week grid's arithmetic, kept pure so it can be proven with no React and no
 * network.
 *
 * Every date here is built from and read as **local** year/month/day parts, through
 * `lib/dates.ts` rather than through a private copy: `toISOString()` is what moved a
 * 31 March 23:30 CEST entry into April and therefore into the wrong monthly export --
 * the file attached to an invoice -- and `new Date("2026-03-10")` parses as UTC midnight
 * and renders as 9 March anywhere behind UTC. Five copies of those four lines had grown
 * across this codebase before they were consolidated; this module adds no sixth.
 *
 * Hour totals are summed in integer hundredths through `lib/decimal.ts`. This is the
 * only arithmetic the browser is allowed to do in this slice, and only for hours: every
 * economic figure arrives from the API already summed (§6), and `no-float-money.test.ts`
 * fails the build if any module adds one.
 */
import { startOfWeek, toIsoDate } from '@/lib/dates'
import { HOURS_SCALE, sumDecimalStrings } from '@/lib/decimal'
import type { TimeEntry } from './queries'

export interface WeekDay {
  /** `YYYY-MM-DD`, the exact shape `time_entries.date` stores and the API expects. */
  iso: string
  /** `lunedì 9 marzo` -- the accessible label for the column, and for each cell in it. */
  label: string
  /** `lun` -- what actually fits in a column head. */
  short: string
}

export interface GridCell {
  entryId: string | null
  hours: string | null
}

const LONG = new Intl.DateTimeFormat('it-IT', { weekday: 'long', day: 'numeric', month: 'long' })
const SHORT = new Intl.DateTimeFormat('it-IT', { weekday: 'short' })

/** The seven days of the week `anchor` falls in, Monday first. */
export function weekDays(anchor: Date): WeekDay[] {
  const monday = startOfWeek(anchor)
  return Array.from({ length: 7 }, (_, offset) => {
    const day = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + offset)
    return { iso: toIsoDate(day), label: LONG.format(day), short: SHORT.format(day) }
  })
}

/**
 * The same weekday `weeks` weeks away.
 *
 * Rebuilt from the calendar parts rather than by adding `weeks * 7 * 86_400_000`: the
 * week that contains a DST transition is 167 or 169 hours long, not 168, so the
 * millisecond route drifts by an hour every spring and -- for an anchor before 01:00 --
 * lands on the previous calendar day.
 */
export function shiftWeek(anchor: Date, weeks: number): Date {
  return new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate() + weeks * 7)
}

/**
 * One row per deal that has an entry this week, one cell per day.
 *
 * A cell holds at most one entry, and that is a deliberate limitation of this screen
 * rather than of the model: `time_entries` allows several entries on the same deal and
 * day (two 14-hour entries are a likely error but not an impossible one, §4.1), and the
 * deal's own Ore tab shows every one of them. The grid exists to attack the failure §13
 * names -- "I never entered Tuesday" -- and a cell that tried to represent three entries
 * would be a worse control for that job. Where several exist, the grid shows the first
 * and the caption points at the full list.
 *
 * Rows are opened only for entries that fall *inside* `days`: which deals also deserve
 * an empty row is the caller's decision (it has the deal list; this function has only
 * the week), and deriving it here would have made "a deal with no hours this week"
 * unrepresentable.
 */
export function buildGrid(
  entries: TimeEntry[],
  days: WeekDay[],
): Map<string, Map<string, GridCell>> {
  const wanted = new Set(days.map((day) => day.iso))
  const grid = new Map<string, Map<string, GridCell>>()
  for (const entry of entries) {
    if (!wanted.has(entry.data)) continue
    let row = grid.get(entry.deal_id)
    if (row === undefined) {
      row = new Map(days.map((day) => [day.iso, { entryId: null, hours: null }]))
      grid.set(entry.deal_id, row)
    }
    const cell = row.get(entry.data)
    // Already taken: keep the first, per the docstring above. Not an overwrite, because
    // the second entry is not a correction of the first -- both are real hours, and the
    // row total below counts only the one the cell can edit.
    if (cell === undefined || cell.entryId !== null) continue
    row.set(entry.data, { entryId: entry.id, hours: entry.hours })
  }
  return grid
}

export function rowTotal(row: Map<string, GridCell>): string {
  return sumDecimalStrings(
    [...row.values()].map((cell) => cell.hours),
    HOURS_SCALE,
  )
}

export function columnTotal(grid: Map<string, Map<string, GridCell>>, iso: string): string {
  return sumDecimalStrings(
    [...grid.values()].map((row) => row.get(iso)?.hours ?? null),
    HOURS_SCALE,
  )
}

export function gridTotal(grid: Map<string, Map<string, GridCell>>): string {
  return sumDecimalStrings(
    [...grid.values()].flatMap((row) => [...row.values()].map((cell) => cell.hours)),
    HOURS_SCALE,
  )
}

/**
 * `"3,5"` -> `"3.5"`. The one place a locale-specific *input* shape is translated in
 * this product: an Italian keyboard produces a comma, and the API's `Decimal` parser
 * wants a dot. Done here rather than in the service, because the service must keep
 * accepting exactly one canonical form -- a backend that guessed between `1,500` as
 * "one and a half" and "one thousand five hundred" would be the ambiguity this
 * translation exists to keep out of it.
 */
export function normaliseHours(raw: string): string {
  return raw.trim().replace(',', '.')
}

/** The inverse, for the value an untouched cell shows: an hour reads back in the shape
 *  it was typed in, so `2,50` does not turn into `2.50` the moment the page reloads. */
export function hoursForInput(value: string | null): string {
  return value === null ? '' : value.replace('.', ',')
}
