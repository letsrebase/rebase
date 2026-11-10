/**
 * The calendar's arithmetic, kept pure so it can be proven with no React and no network.
 *
 * Every date is built from and read as **local** year/month/day parts, through
 * `lib/dates.ts` and never through a private copy: `toISOString()` is what moved a
 * 31 March 23:30 CEST entry into April, and `new Date("2026-03-10")` parses as UTC
 * midnight and renders as 9 March anywhere behind UTC. `features/time/week.ts` says the
 * same thing about the week; this module adds no sixth copy of those four lines.
 *
 * Hours are summed in integer hundredths through `lib/decimal.ts`, the only arithmetic
 * the browser is allowed to do: every economic figure arrives already summed from the
 * API, and `no-float-money.test.ts` fails the build if a module adds one.
 */
import { toIsoDate, toIsoMonth } from '@/lib/dates'

export interface MonthCell {
  /** `YYYY-MM-DD`: the shape the API answers with and the one it expects back. */
  iso: string
  /** The day of the month, which is all a cell has room to print. */
  day: number
  /** `false` for the leading and trailing days that belong to the neighbouring months. */
  inMonth: boolean
}

/** `lun` … `dom`, in the order the grid draws them. */
export const WEEKDAY_LABELS = ['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom'] as const

/**
 * `YYYY-MM` for the month `value` falls in -- re-exported through this module so a
 * caller never reaches for `toISOString().slice(0, 7)`, which is the same trap one field
 * narrower.
 */
export function monthOf(value: Date): string {
  return toIsoMonth(value)
}

/**
 * `AAAA-MM` shifted by `months`, forwards or back.
 *
 * Built by setting the day to 1 *before* shifting the month, which is the whole reason
 * this is a function and not `setMonth(m + n)` at a call site: on 31 March,
 * `setMonth(1)` asks for 31 February and JavaScript answers 3 March, so «previous
 * month» from a 31st silently skips a month. Anchoring to the first makes every shift
 * exact.
 */
export function shiftMonth(month: string, months: number): string {
  const first = firstOfMonth(month)
  first.setMonth(first.getMonth() + months)
  return toIsoMonth(first)
}

/** The first day of `AAAA-MM`, at local midnight. */
export function firstOfMonth(yearMonth: string): Date {
  const [year, month] = yearMonth.split('-').map(Number)
  if (year === undefined || month === undefined) return new Date()
  if (!Number.isFinite(year) || !Number.isFinite(month)) return new Date()
  return new Date(year, month - 1, 1)
}

/**
 * The month as whole weeks, Monday to Sunday: the cells a grid draws, including the
 * neighbours' days that fill the first and last row.
 *
 * Always whole weeks, so the grid is a rectangle and no row has holes in it -- a
 * calendar whose first row is three empty divs is a calendar that reflows when the month
 * changes. The neighbours' days carry `inMonth: false` and are drawn quiet; they are
 * real days and not blanks, because clicking 1 October from September's last row and
 * getting nothing would be a dead cell.
 */
export function monthCells(yearMonth: string): MonthCell[] {
  const first = firstOfMonth(yearMonth)
  const month = first.getMonth()
  // `getDay()` is 0 for Sunday, and this grid starts on Monday: `(day + 6) % 7` maps
  // Monday to 0 and Sunday to 6, which is the offset of the first cell.
  const lead = (first.getDay() + 6) % 7
  const cells: MonthCell[] = []
  const cursor = new Date(first)
  cursor.setDate(cursor.getDate() - lead)
  // Six weeks is the most a month can span (31 days starting on a Sunday), and the loop
  // stops as soon as a whole week has passed the month's end rather than always drawing
  // six: a five-week month with a sixth empty row reads as a month with a week nobody
  // works.
  for (let index = 0; index < 42; index += 1) {
    const inMonth = cursor.getMonth() === month
    cells.push({ iso: toIsoDate(cursor), day: cursor.getDate(), inMonth })
    const finishedWeek = index % 7 === 6
    const pastEnd = cursor.getMonth() !== month && cursor > first
    if (finishedWeek && pastEnd) break
    cursor.setDate(cursor.getDate() + 1)
  }
  return cells
}

/** `settembre 2026`, for the page's own description. */
const monthLabelFormatter = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric' })

export function monthLabel(month: string): string {
  return monthLabelFormatter.format(firstOfMonth(month))
}
