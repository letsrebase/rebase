import { formatIsoDateItalian, isoYearMonth } from '@/lib/dates'

/**
 * The periods an export is made for: a month, a quarter or a year, written as the first and the
 * last day (both included, as the API takes them). Only the calendar is worked out here; what a
 * period holds is the server's to say.
 *
 * «Anno» is the calendar year: the design says «a year» and the fiscal year end of a legal
 * entity (REB-964) is not read yet, so a company whose year ends in another month still picks the
 * calendar year or the quarters it needs.
 */
export type PeriodKind = 'month' | 'quarter' | 'year'

export const KIND_LABELS: Record<PeriodKind, string> = {
  month: 'Mese',
  quarter: 'Trimestre',
  year: 'Anno',
}

export const KINDS: readonly PeriodKind[] = ['month', 'quarter', 'year']

export interface PeriodOption {
  /** `month:2026-10`, `quarter:2026-3`, `year:2026`: unique among the options of one kind. */
  value: string
  from: string
  to: string
  label: string
  /** The period as it is named once it is made, with no «(in corso)». */
  name: string
  /** The period holds today: the picker tags it «(in corso)» and never defaults to it. */
  current: boolean
}

const HOW_MANY: Record<PeriodKind, number> = { month: 24, quarter: 12, year: 6 }
const MONTHS_PER_QUARTER = 3

const pad = (value: number) => String(value).padStart(2, '0')
const iso = (year: number, month: number, day: number) => `${year}-${pad(month)}-${pad(day)}`
const lastDay = (year: number, month: number) => new Date(year, month, 0).getDate()

const monthName = new Intl.DateTimeFormat('it-IT', { month: 'long' })

function monthLabel(year: number, month: number): string {
  const name = monthName.format(new Date(year, month - 1, 1))
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} ${year}`
}

const quarterLabel = (year: number, quarter: number) => `${quarter}° trimestre ${year}`

function option(
  value: string,
  from: string,
  to: string,
  label: string,
  today: string,
): PeriodOption {
  const current = from <= today && today <= to
  return { value, from, to, label: current ? `${label} (in corso)` : label, name: label, current }
}

/** The periods of `kind` from the one that holds `today` back, newest first. */
export function periodsOf(kind: PeriodKind, now: Date): PeriodOption[] {
  const today = iso(now.getFullYear(), now.getMonth() + 1, now.getDate())
  const options: PeriodOption[] = []
  for (let back = 0; back < HOW_MANY[kind]; back += 1) {
    if (kind === 'month') {
      const first = new Date(now.getFullYear(), now.getMonth() - back, 1)
      const year = first.getFullYear()
      const month = first.getMonth() + 1
      options.push(
        option(
          `month:${year}-${pad(month)}`,
          iso(year, month, 1),
          iso(year, month, lastDay(year, month)),
          monthLabel(year, month),
          today,
        ),
      )
    } else if (kind === 'quarter') {
      const index = Math.floor(now.getMonth() / MONTHS_PER_QUARTER) - back
      const year = now.getFullYear() + Math.floor(index / 4)
      const quarter = (((index % 4) + 4) % 4) + 1
      const firstMonth = (quarter - 1) * MONTHS_PER_QUARTER + 1
      const lastMonth = firstMonth + MONTHS_PER_QUARTER - 1
      options.push(
        option(
          `quarter:${year}-${quarter}`,
          iso(year, firstMonth, 1),
          iso(year, lastMonth, lastDay(year, lastMonth)),
          quarterLabel(year, quarter),
          today,
        ),
      )
    } else {
      const year = now.getFullYear() - back
      options.push(option(`year:${year}`, iso(year, 1, 1), iso(year, 12, 31), String(year), today))
    }
  }
  return options
}

/** The period the picker opens on: the last one that is over. */
export function defaultPeriod(options: readonly PeriodOption[]): PeriodOption | undefined {
  return options.find((candidate) => !candidate.current)
}

/** «dal 01/07/2026 al 30/09/2026», written beside the picker. */
export function rangeSentence(from: string, to: string): string {
  return `dal ${formatIsoDateItalian(from)} al ${formatIsoDateItalian(to)}`
}

/**
 * What a stored export's period is called: «3° trimestre 2026», «Ottobre 2026» or «2026» when it
 * is exactly one, the range otherwise (an agent may build any period up to a year).
 */
export function periodName(from: string, to: string): string {
  const start = isoYearMonth(from)
  if (start !== null && from.endsWith('-01')) {
    const { year, month } = start
    if (from === iso(year, 1, 1) && to === iso(year, 12, 31)) return String(year)
    if (to === iso(year, month, lastDay(year, month))) return monthLabel(year, month)
    const quarter = Math.floor((month - 1) / MONTHS_PER_QUARTER) + 1
    const lastMonth = quarter * MONTHS_PER_QUARTER
    if (month === lastMonth - 2 && to === iso(year, lastMonth, lastDay(year, lastMonth))) {
      return quarterLabel(year, quarter)
    }
  }
  return rangeSentence(from, to)
}
