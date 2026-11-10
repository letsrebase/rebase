import { daysCount } from '@/features/cash/days'
import { formatIsoDateItalian, formatIsoDayMonth, formatIsoMonthYear, isoYearMonth } from '@/lib/dates'
import type { components } from '@/lib/api-types'

export type CashAlert = components['schemas']['CashAlert']

/** The month alone, `ottobre`, for the current year; `ottobre 2025` for any other. */
function monthName(iso: string, year: number | null): string {
  const named = formatIsoMonthYear(iso)
  return isoYearMonth(iso)?.year === year ? named.replace(/\s+\d{4}$/, '') : named
}

/** What a missing statement covers: a month by its name, anything longer by its two dates. */
function periodOf(alert: CashAlert, year: number | null): string {
  const from = alert.period_from
  const to = alert.period_to
  if (!from) return 'del periodo'
  const first = isoYearMonth(from)
  const last = to ? isoYearMonth(to) : null
  if (!to || (first && last && first.year === last.year && first.month === last.month)) {
    return `di ${monthName(from, year)}`
  }
  return `dal ${formatIsoDateItalian(from)} al ${formatIsoDateItalian(to)}`
}

/**
 * The one sentence of a cash alert (brief, «The Home's money figures on the phone»): the
 * browser only composes it from the server's structured fields, the dates and the number of
 * days are the server's. `today` is the server's too: a month of another year names its year.
 */
export function cashAlertSentence(alert: CashAlert, today: string): string {
  switch (alert.kind) {
    case 'below_zero':
      return alert.week_start && alert.week_end
        ? `Il saldo previsto scende sotto zero nella settimana dal ${formatIsoDayMonth(alert.week_start)} al ${formatIsoDateItalian(alert.week_end)}`
        : 'Il saldo previsto scende sotto zero'
    case 'missing_statement':
      return `Manca l’estratto conto ${periodOf(alert, isoYearMonth(today)?.year ?? null)}${alert.account_name ? ` per ${alert.account_name}` : ''}`
    case 'stale_balance':
      return `Il saldo${alert.account_name ? ` di ${alert.account_name}` : ''} non si aggiorna${alert.days != null ? ` da ${daysCount(alert.days)}` : ''}`
  }
}

/** `below_zero` is the one that is already wrong; the other two are a statement that is late. */
export const ALERT_DOT: Record<CashAlert['kind'], 'danger' | 'gold'> = {
  below_zero: 'danger',
  missing_statement: 'gold',
  stale_balance: 'gold',
}
