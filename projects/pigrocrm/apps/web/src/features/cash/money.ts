import { money } from '@/features/dashboard/format'

const formatters = new Map<string, Intl.NumberFormat>()

/**
 * An amount in its own currency: euro as everywhere (`1.464,00 €`), another one with its code
 * («1.350,00 USD»). The string the server sent goes into the formatter as it is, never through a
 * float. One helper for the schedule, the account page and the Home's figures.
 */
export function moneyIn(value: string, currency: string): string {
  if (currency === 'EUR') return money(value)
  try {
    let format = formatters.get(currency)
    if (!format) {
      format = new Intl.NumberFormat('it-IT', { style: 'currency', currency, useGrouping: 'always' })
      formatters.set(currency, format)
    }
    return format.format(value as `${number}`)
  } catch {
    return `${value.replace('.', ',')} ${currency}`
  }
}
