/**
 * «Consuntivo»'s arithmetic (REB-503). The page asks the hub once for the whole
 * engagement (`matches.report`) and slices it here, by month, in the browser: the days of
 * the period, their weeks and months, and the invoices they sit on with the period's
 * hours on each. Pure functions over the API's strings: hours are summed as whole
 * hundredths, never as floats, and written back with two places as the API writes them.
 */
import type { MatchReport, ReportDay, ReportInvoice, ReportMonth, ReportWeek } from './api'
import { formatDecimal } from './format'

/** «Tutto l'incarico» in the URL, `?mese=tutto`; a month is `?mese=2026-10`. */
export const TUTTO = 'tutto'
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/
const DAY_MS = 86_400_000

/** The period `validateSearch` keeps: a month as `YYYY-MM` or `tutto`, anything else
 *  dropped for the page's default, the current month. */
export function periodParam(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  return value === TUTTO || MONTH.test(value) ? value : undefined
}

/** Today as `YYYY-MM-DD` in Rome, the day the hub (`rome_today`) and the CRM both ask
 *  the report up to, whatever the browser's own time zone. */
export function romeToday(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Rome',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')}`
}

/** Every month from `first`'s to `last`'s, both included, oldest first. */
export function monthsBetween(first: string, last: string): string[] {
  let [year, month] = first.split('-').map(Number) as [number, number]
  const end = last.slice(0, 7)
  const months: string[] = []
  for (;;) {
    const current = `${year}-${String(month).padStart(2, '0')}`
    if (current > end) return months
    months.push(current)
    month += 1
    if (month > 12) {
      month = 1
      year += 1
    }
  }
}

/** The months the period selector offers, newest first: from the letter's start (or the
 *  first hour, when the page does not know the start) to the current month, any month
 *  an hour falls in, and the month a shared link names. A letter that starts after
 *  today offers the current month alone. */
export function periodMonths(
  start: string | null,
  today: string,
  days: readonly ReportDay[],
  selected?: string,
): string[] {
  const current = today.slice(0, 7)
  const from = start ?? days[0]?.data ?? today
  const months = new Set(monthsBetween(from.slice(0, 7) < current ? from : today, today))
  for (const day of days) months.add(day.data.slice(0, 7))
  if (selected !== undefined && selected !== TUTTO) months.add(selected)
  return [...months].sort().reverse()
}

/** The days of the period, in the report's own order. */
export function sliceDays(days: readonly ReportDay[], period: string): ReportDay[] {
  return period === TUTTO ? [...days] : days.filter((day) => day.data.startsWith(`${period}-`))
}

function hundredths(value: string): number {
  const [whole = '0', fraction = ''] = value.trim().split('.')
  const units = Math.abs(Number(whole)) * 100 + Number(fraction.padEnd(2, '0').slice(0, 2))
  return whole.startsWith('-') ? -units : units
}

function fromHundredths(total: number): string {
  const units = Math.abs(total)
  return `${total < 0 ? '-' : ''}${Math.floor(units / 100)}.${String(units % 100).padStart(2, '0')}`
}

/** The API's decimal strings added up exactly: `0.10` and `0.20` make `0.30`. */
export function sumHours(values: readonly string[]): string {
  return fromHundredths(values.reduce((total, value) => total + hundredths(value), 0))
}

function isoDate(time: number): string {
  return new Date(time).toISOString().slice(0, 10)
}

/** A day's ISO week, as the hub's report names it: Monday to Sunday, numbered in the
 *  year its Thursday falls in, so 31 December 2026 and 1 January 2027 are both
 *  «2026-W53». */
export function isoWeek(value: string): Omit<ReportWeek, 'ore'> {
  const day = Date.parse(`${value}T00:00:00Z`)
  const monday = day - ((new Date(day).getUTCDay() + 6) % 7) * DAY_MS
  const thursday = monday + 3 * DAY_MS
  const year = new Date(thursday).getUTCFullYear()
  const week = Math.floor((thursday - Date.UTC(year, 0, 1)) / DAY_MS / 7) + 1
  return {
    settimana: `${year}-W${String(week).padStart(2, '0')}`,
    da: isoDate(monday),
    a: isoDate(monday + 6 * DAY_MS),
  }
}

/** The hours of the given days by ISO week, oldest first: a week the period cuts keeps
 *  its full Monday-to-Sunday span and only the period's hours. */
export function perWeek(days: readonly ReportDay[]): ReportWeek[] {
  const weeks = new Map<string, { week: Omit<ReportWeek, 'ore'>; hours: string[] }>()
  for (const day of days) {
    const week = isoWeek(day.data)
    const seen = weeks.get(week.settimana) ?? { week, hours: [] }
    seen.hours.push(day.ore)
    weeks.set(week.settimana, seen)
  }
  return [...weeks.values()]
    .sort((x, y) => (x.week.settimana < y.week.settimana ? -1 : 1))
    .map(({ week, hours }) => ({ ...week, ore: sumHours(hours) }))
}

/** The hours of the given days by month, oldest first. */
export function perMonth(days: readonly ReportDay[]): ReportMonth[] {
  const months = new Map<string, string[]>()
  for (const day of days) {
    const mese = day.data.slice(0, 7)
    months.set(mese, [...(months.get(mese) ?? []), day.ore])
  }
  return [...months.entries()]
    .sort(([x], [y]) => (x < y ? -1 : 1))
    .map(([mese, hours]) => ({ mese, ore: sumHours(hours) }))
}

/** An invoice as a day's `fatture` names it (the hub's `_invoice_label`): the number
 *  alone for a `fattura`, its type first for anything else, «proforma 4/2026». */
export function invoiceLabel(invoice: Pick<ReportInvoice, 'numero' | 'tipo'>): string {
  return invoice.tipo === 'fattura' ? invoice.numero : `${invoice.tipo} ${invoice.numero}`
}

/** The invoices of the period, in the report's order (newest first), each with the
 *  period's hours on it. «Tutto l'incarico» is every invoice with all its hours, the
 *  CRM's own figure. A month shows only the invoices its days sit on: an invoice whose
 *  days all fall in the month keeps the CRM's figure too, and one that spans months
 *  (12/2026 over September and October) shows each month the hours that month's days
 *  hold on it, each day's own share from `ore_per_fattura`. A day whose entries sit
 *  partly on an invoice and partly on another or on none used to give its whole total
 *  to each invoice there, since the report carried only the day's total; with each
 *  invoice's share of the day in the report (REB-505) that limit is gone: 8 hours with 3
 *  on 14/2026 give 14/2026 its 3. */
export function periodInvoices(
  report: Pick<MatchReport, 'per_giorno' | 'fatture'>,
  period: string,
): ReportInvoice[] {
  if (period === TUTTO) return [...report.fatture]
  const shown = sliceDays(report.per_giorno, period)
  return report.fatture.flatMap((invoice) => {
    const label = invoiceLabel(invoice)
    const sitsOn = (day: ReportDay) => day.fatture.includes(label)
    const here = shown.filter(sitsOn)
    if (here.length === 0) return []
    if (here.length === report.per_giorno.filter(sitsOn).length) return [invoice]
    const shares = here.flatMap((day) =>
      day.ore_per_fattura.filter((share) => invoiceLabel(share) === label).map((share) => share.ore),
    )
    return [{ ...invoice, ore: sumHours(shares) }]
  })
}

function counted(value: string, one: string, many: string): string {
  return `${formatDecimal(value)} ${hundredths(value) === 100 ? one : many}`
}

/** «96 ore, 12 giorni su 40 previsti (30%)», the engagement against the letter's
 *  estimate; «96 ore, 12 giorni» for a match without one. */
export function progressLine(
  report: Pick<MatchReport, 'totale_ore' | 'giorni_equivalenti' | 'giorni_previsti' | 'avanzamento'>,
): string {
  const done = `${counted(report.totale_ore, 'ora', 'ore')}, ${counted(report.giorni_equivalenti, 'giorno', 'giorni')}`
  if (!report.giorni_previsti || report.avanzamento === null) return done
  const previsti = report.giorni_previsti === 1 ? 'previsto' : 'previsti'
  return `${done} su ${report.giorni_previsti} ${previsti} (${formatDecimal(report.avanzamento)}%)`
}
