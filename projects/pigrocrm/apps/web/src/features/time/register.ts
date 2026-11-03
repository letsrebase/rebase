/**
 * The register's grouping, pure and provable with no React: `TimeRegister.tsx` keeps to
 * exporting a component (eslint's `react-refresh/only-export-components`).
 */
import { formatIsoDateItalian } from '@/lib/dates'
import { HOURS_SCALE, sumDecimalStrings } from '@/lib/decimal'
import type { TimeEntry } from './queries'
import type { WeekDay } from './week'

export interface DayGroup {
  iso: string
  label: string
  total: string
  entries: TimeEntry[]
}

/** Entries grouped by day, latest day first, latest entry first inside a day -- the
 *  order Toggl and Clockify both read in: what did I just do. Pure, so it is provable. */
export function groupByDay(entries: TimeEntry[], days: WeekDay[]): DayGroup[] {
  const labels = new Map(days.map((day) => [day.iso, day.label]))
  const byDay = new Map<string, TimeEntry[]>()
  for (const entry of entries) {
    const bucket = byDay.get(entry.date) ?? []
    bucket.push(entry)
    byDay.set(entry.date, bucket)
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
    .map(([iso, items]) => ({
      iso,
      label: labels.get(iso) ?? formatIsoDateItalian(iso),
      total: sumDecimalStrings(
        items.map((item) => item.hours),
        HOURS_SCALE,
      ),
      entries: [...items].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
    }))
}
