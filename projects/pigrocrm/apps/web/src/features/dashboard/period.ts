/**
 * The period a dashboard answers for, and the three presets that pick one.
 *
 * Separate from `PeriodPicker.tsx` rather than exported beside it: a module exporting a
 * component and a function is what `react-refresh/only-export-components` forbids, and the
 * codebase's answer to that is a second module, not a rule override.
 *
 * These helpers produce plain `YYYY-MM-DD` strings and never `Date` objects. A `Date` is a
 * *timestamp*, and `new Date("2026-03-01").toISOString()` is UTC midnight: formatting it
 * back renders 28 February anywhere behind UTC, which on a dashboard silently answers for
 * the wrong month. Every date built here comes from the browser's *local* calendar parts.
 */

export type Period = { da: string; a: string }

function iso(year: number, monthIndex: number, day: number): string {
  const month = `${monthIndex + 1}`.padStart(2, '0')
  return `${year}-${month}-${`${day}`.padStart(2, '0')}`
}

function lastDayOfMonth(year: number, monthIndex: number): number {
  // Day 0 of the next month is the last day of this one -- no leap-year table needed.
  return new Date(year, monthIndex + 1, 0).getDate()
}

export function currentMonth(): Period {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth()
  return { da: iso(year, month, 1), a: iso(year, month, lastDayOfMonth(year, month)) }
}

export function presetQuarter(): Period {
  const now = new Date()
  const year = now.getFullYear()
  const firstMonthOfQuarter = Math.floor(now.getMonth() / 3) * 3
  const lastMonthOfQuarter = firstMonthOfQuarter + 2
  return {
    da: iso(year, firstMonthOfQuarter, 1),
    a: iso(year, lastMonthOfQuarter, lastDayOfMonth(year, lastMonthOfQuarter)),
  }
}

export function presetYear(): Period {
  const year = new Date().getFullYear()
  return { da: iso(year, 0, 1), a: iso(year, 11, 31) }
}
