/**
 * The four lines that convert between a backend `date` column and what a person in
 * Italy reads, plus the two that go the other way.
 *
 * This module exists because the same four lines had been written five times --
 * `components/DynamicFieldRenderer.tsx`, `features/deals/columns.tsx`,
 * `features/time/columns.tsx`, `features/time/formValues.ts` and
 * `features/time/TimeReportButtons.tsx` -- each carrying its own paragraph explaining
 * the same timezone trap. Every copy was correct; each was justified, at the time, by
 * not wanting to widen an eslint `allowExportNames` override to share four lines. That
 * reasoning holds for one copy and stops holding at five: the next person to fix a date
 * bug has to find all of them. A plain `lib/` module has no
 * `react-refresh/only-export-components` problem to work around in the first place,
 * because it exports no component.
 */

/**
 * An ISO `YYYY-MM-DD` as a `Date` at *local* midnight, or `null` if it is not one.
 *
 * `new Date("2026-08-06")` parses as UTC *midnight*; formatting that with
 * `Intl.DateTimeFormat` renders it in whichever zone the browser is in. East of
 * Greenwich it is still 6 August, but anywhere behind UTC -- all of the Americas --
 * UTC midnight is the previous evening, so the displayed date silently loses a day.
 * Building the `Date` from its year/month/day parts puts construction and formatting in
 * the same zone, so the calendar day survives wherever this runs.
 *
 * Two guards, not one. The `undefined` check is what all three original copies of this
 * carried, and it is what `noUncheckedIndexedAccess` needs to narrow a `.split` result
 * down to three numbers -- but it only catches a *short* value. `"non-una-data".split("-")`
 * has exactly three parts, none of them `undefined`, all three `NaN`: it sailed past that
 * guard, `new Date(NaN, NaN, NaN)` is an Invalid Date, and `Intl.DateTimeFormat.format`
 * throws `RangeError: Invalid time value` on one. Every copy documented a fallback to the
 * raw string and then crashed the surrounding cell instead. `Number.isFinite` is the half
 * that was missing; it narrows nothing on its own (it is typed
 * `(value: unknown) => boolean`), which is why both checks are here rather than one.
 *
 * Private, and returning `null` rather than the raw string, so that each formatter below
 * decides its own fallback while there is exactly one place that knows the trap.
 */
function localDateFromIso(value: string): Date | null {
  const [year, month, day] = value.split('-').map(Number)
  if (year === undefined || month === undefined || day === undefined) return null
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null
  return new Date(year, month - 1, day)
}

/** Renders an ISO `YYYY-MM-DD` string as `gg/mm/aaaa`. */
export function formatIsoDateItalian(value: string): string {
  const local = localDateFromIso(value)
  if (local === null) return value
  return new Intl.DateTimeFormat('it-IT').format(local)
}

const dayMonthFormatter = new Intl.DateTimeFormat('it-IT', { day: '2-digit', month: '2-digit' })

/**
 * The same date as `gg/mm`, for a label that has to carry a day without the year.
 *
 * Zero-padded, which `it-IT`'s own default is not: `Intl.DateTimeFormat('it-IT').format`
 * renders 19 March as `19/3/2026`, so a column of dates would not line up and `19/03`
 * would not be findable. The dashboard's week of missing days is the call site this
 * exists for; the year is redundant there because the heading already names the week.
 */
export function formatIsoDayMonth(value: string): string {
  const local = localDateFromIso(value)
  if (local === null) return value
  return dayMonthFormatter.format(local)
}

const monthYearFormatter = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric' })

/**
 * The month of an ISO date, named: `ottobre 2026`. The scadenziario's cash-by-month rows
 * carry the month's first day, and a bar labelled `01/10/2026` would read as a day.
 */
export function formatIsoMonthYear(value: string): string {
  const local = localDateFromIso(value)
  if (local === null) return value
  return monthYearFormatter.format(local)
}

const weekdayFormatter = new Intl.DateTimeFormat('it-IT', { weekday: 'short' })

/**
 * The weekday of an ISO date, abbreviated: `lun`, `mar`, ...
 *
 * A seven-column week is read by weekday, not by date -- and a date under each column
 * would also make a day that *was* logged with zero hours indistinguishable, in a text
 * search, from one that was never logged at all. The days actually missing are named
 * with `formatIsoDayMonth` where that distinction is the point.
 */
export function formatIsoWeekday(value: string): string {
  const local = localDateFromIso(value)
  if (local === null) return value
  return weekdayFormatter.format(local)
}

/**
 * A `Date` as the ISO `YYYY-MM-DD` string the API expects, read from its *local* parts.
 *
 * The mirror image of the trap above, and the reason this is never
 * `toISOString().slice(0, 10)`: `toISOString` converts to UTC first, so anywhere east of
 * Greenwich late in the evening a form seeded that way opens pre-filled with *tomorrow*
 * -- and the backend's `_check_not_future` then refuses a date the user never chose.
 */
export function toIsoDate(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${value.getFullYear()}-${month}-${day}`
}

/**
 * The same, one component shorter: `YYYY-MM`, for an `<input type="month">`. On the last
 * evening of a month east of Greenwich, the UTC route would open the picker on the month
 * that has not started yet.
 */
export function toIsoMonth(value: Date): string {
  return toIsoDate(value).slice(0, 7)
}

/**
 * The Monday of the week `value` falls in, at local midnight.
 *
 * Monday-first is a product decision about what a working week looks like in Italy, not
 * a locale lookup: `Intl.Locale.prototype.getWeekInfo` would answer the same today, but
 * an ICU default that changed would silently reorder the columns of the hours grid, and
 * a grid whose column order can move is a grid nobody can read a total off.
 *
 * Lives here rather than in `features/time/week.ts` for the reason this module exists at
 * all: it is the same local-parts date arithmetic as its three neighbours, and the next
 * person to fix a date bug should find it in one place. Reconstructing the `Date` from
 * year/month/day drops the time of day too, so an anchor at 23:30 and one at 08:00 name
 * the same week -- and `setDate` with a negative argument rolls the month and the year
 * back in *local* time, which is why this never subtracts milliseconds (a DST boundary
 * inside the subtracted span would land it an hour, and from 00:30 a whole day, early).
 */
export function startOfWeek(value: Date): Date {
  const monday = new Date(value.getFullYear(), value.getMonth(), value.getDate())
  // `getDay()` numbers Sunday 0 .. Saturday 6, so a bare `- getDay()` would leave Sunday
  // on itself and start every other day's week a day early. `(day + 6) % 7` renumbers it
  // Monday 0 .. Sunday 6, which is the offset back to Monday.
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7))
  return monday
}
