/** An amount as an Italian types it, in the machine form the API takes. With a comma,
 *  the comma is the decimal and every dot a thousands separator («1.234,50» is 1234.50).
 *  Without one, dots that group the digits in threes are thousands too («12.000» is
 *  12000, «1.500» is 1500), while a single dot before one or two digits is already the
 *  machine form («480.50»), which is also what the API hands back («1500.00»). Anything
 *  else goes as typed, for the field's own rule or the API to refuse.
 *
 *  Every amount field reads through this one function: the day rate, the daily budget
 *  and the fee of a letter of engagement (REB-485). */
export function machineAmount(value: string): string {
  const text = value.trim()
  if (text.includes(',')) return text.replaceAll('.', '').replace(',', '.')
  if (/^\d{1,3}(\.\d{3})+$/.test(text)) return text.replaceAll('.', '')
  return text
}

/** The words every amount field uses for what is not an amount: the wizards, the member
 *  area, the admin's dialogs and the list filters. */
export const AMOUNT_PROBLEM = 'Serve una cifra, in euro.'

/** The number an amount field holds, read the Italian way. `NaN` for anything that is not
 *  an amount, a blank field included: `Number('')` would make it 0. */
export function amountNumber(value: string): number {
  const text = machineAmount(value)
  return text === '' ? Number.NaN : Number(text)
}

/** A list filter's amount in the machine form the API takes, or `undefined` when what is
 *  typed is not an amount of zero or more: the filter then narrows nothing, and the field
 *  says why. */
export function amountFilter(value: string | undefined): string | undefined {
  if (value === undefined) return undefined
  const number = amountNumber(value)
  return Number.isFinite(number) && number >= 0 ? machineAmount(value) : undefined
}
