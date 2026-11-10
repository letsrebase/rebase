import { daysBetweenIso } from '@/lib/dates'
import {
  reportState,
  type DaysReport,
  type EngagementPayment,
  type MonthlyReport,
  type PaymentView,
  type ReportMonth,
  type ReportState,
} from './labels'

/**
 * The rows of the table «I mesi» (the approved brief, «The engagement page»): the report's months
 * with the payment items of each, then one summary row for the forecasts that come after the last
 * month the report reads, and the payments that name no month. Everything here is a join of what
 * the server sent; no amount, date or quantity is worked out (the brief's rule), the only counts are
 * of rows and of days a person ticked.
 */

export interface MonthRow {
  /** `2026-10`: what a payment's `month` and the record body of the arrival call both start with. */
  key: string
  /** The month's first day, `2026-10-01`, as the report names it. */
  month: string
  /** The report's own line for the month; `null` for a month only a payment names. */
  report: ReportMonth | null
  monthly: MonthlyReport | null
  /** The month is over: the «Rendiconto» it reads is «Non ricevuto» and not «Non ancora». */
  ended: boolean
  state: ReportState
  payments: EngagementPayment[]
}

export interface FutureRow {
  /** The months the forecasts are for, counted, not added: `3` for «3 mesi previsti». */
  months: number
  firstMonth: string
  lastMonth: string
}

export interface MonthPlan {
  rows: MonthRow[]
  future: FutureRow | null
  /** The payments with no month: a lump sum, or an item the letter does not date to a month. */
  others: EngagementPayment[]
}

/** `2026-10-01` or `2026-10` as `2026-10`. */
export function monthKey(value: string): string {
  return value.slice(0, 7)
}

/** `2026-10` as the first day of the month, the shape `monthLabel` reads. */
export function monthStart(key: string): string {
  return key.length === 7 ? `${key}-01` : key
}

function byKey(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/**
 * Joins the report's months and the payments by the payment's `month`. `today` is `YYYY-MM-DD`, passed
 * in so a test can fix it. A forecast whose month is later than the last report month goes into the
 * summary row; any other payment that names a month the report does not read gets a row of its own,
 * and one with no month goes into `others`.
 */
export function planMonths(
  report: DaysReport | null | undefined,
  payments: readonly EngagementPayment[],
  today: string,
): MonthPlan {
  const currentKey = monthKey(today)
  const reportMonths = [...(report?.months ?? [])].sort((a, b) => byKey(a.month, b.month))
  const lastMonth = reportMonths.at(-1)
  const lastKey = lastMonth === undefined ? null : monthKey(lastMonth.month)

  const byMonth = new Map<string, EngagementPayment[]>()
  const others: EngagementPayment[] = []
  for (const payment of payments) {
    if (payment.month === null) {
      others.push(payment)
      continue
    }
    const key = monthKey(payment.month)
    byMonth.set(key, [...(byMonth.get(key) ?? []), payment])
  }

  const rowFor = (key: string, month: ReportMonth | null): MonthRow => {
    const monthly = month?.monthly_report ?? null
    const ended = key < currentKey
    return {
      key,
      month: month?.month ?? monthStart(key),
      report: month,
      monthly,
      ended,
      state: reportState(monthly, ended),
      payments: byMonth.get(key) ?? [],
    }
  }

  const rows = reportMonths.map((month) => rowFor(monthKey(month.month), month))
  const known = new Set(rows.map((row) => row.key))
  const futureKeys = new Set<string>()
  for (const [key, items] of byMonth) {
    if (known.has(key)) continue
    if (lastKey !== null && key > lastKey && items.every((item) => item.state === 'forecast')) {
      futureKeys.add(key)
      continue
    }
    rows.push(rowFor(key, null))
  }
  rows.sort((a, b) => byKey(a.key, b.key))

  const sorted = [...futureKeys].sort(byKey)
  const first = sorted[0]
  const last = sorted[sorted.length - 1]
  return {
    rows,
    future:
      first === undefined || last === undefined
        ? null
        : { months: sorted.length, firstMonth: monthStart(first), lastMonth: monthStart(last) },
    others,
  }
}

/** How a payment reads: a forecast until the invoice, «da pagare» once it is expected, late past its date. */
export function paymentView(payment: EngagementPayment, today: string): PaymentView {
  if (payment.state === 'settled') return 'paid'
  if (payment.state === 'forecast') return 'forecast'
  return payment.due_date !== null && payment.due_date < today ? 'late' : 'to_pay'
}

/** «Scaduto da 3 giorni»: the late pill's word carries the days the server's date is behind today. */
export function lateWords(payment: EngagementPayment, today: string): string {
  const days = payment.due_date === null ? null : daysBetweenIso(payment.due_date, today)
  if (days === null || days < 1) return 'Scaduto'
  return `Scaduto da ${days} ${days === 1 ? 'giorno' : 'giorni'}`
}

/** The decision a month waits for: received and still inside the days the freelancer's report allows. */
export function awaitsDecision(row: MonthRow): boolean {
  return row.monthly?.effective_state === 'requested'
}

/** A finished month nobody recorded the arrival of. */
export function awaitsArrival(row: MonthRow): boolean {
  return row.monthly === null && row.ended
}
