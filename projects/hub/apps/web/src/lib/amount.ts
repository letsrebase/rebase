/* An amount as an Italian types it (REB-485). The same grammar as the core's
 * `rebase_core.amounts`, and both run the table in `amount_cases.json` beside this file:
 *
 * - with a comma, the comma is the decimal and dots may only group the digits before it in
 *   threes («1.234,50» is 1234.50, «1,5» is 1.5); anything else with a comma is refused,
 *   English notation included («1,000.00» is not 1.00);
 * - without one, a dot before exactly three digits is a thousands separator («1.500» is
 *   1500, «100.000» is 100000), so a number shaped that way must be thousands throughout
 *   («0.500» and «1234.567» are refused); any other dot is the decimal point («480.50»,
 *   «1500.00», what the API answers with);
 * - ASCII digits only, no sign, no exponent, no spaces or other separators inside.
 *
 * Precision is not the parser's business: the fields refuse more than two decimals
 * themselves (`euroAmount`).
 *
 * What the web sends is never the parser's output but `sentAmount`'s: two decimals, a dot,
 * no grouping. A reader that reads the Italian way again (the core's `italian_amount`)
 * takes «1.500» for 1500, so «1,5» must not leave as «1.500»; «1.50» it cannot misread. */
const COMMA = /^(?:[0-9]+|[1-9][0-9]{0,2}(?:\.[0-9]{3})+),[0-9]+$/
const THOUSANDS = /^[1-9][0-9]{0,2}(?:\.[0-9]{3})+$/
// The core's `_MACHINE`: a decimal point before anything but exactly three digits.
const MACHINE = /^[0-9]+(?:\.(?:[0-9]{1,2}|[0-9]{4,}))?$/

/** The machine form of what was typed, or `null` when it is not an amount. `trim()` drops
 *  whitespace, NBSP and a BOM alike, as the core does. */
function readAmount(value: string): string | null {
  const text = value.trim()
  if (text.includes(',')) return COMMA.test(text) ? text.replaceAll('.', '').replace(',', '.') : null
  if (THOUSANDS.test(text)) return text.replaceAll('.', '')
  return MACHINE.test(text) ? text : null
}

/** The words every amount field uses for what is not an amount: the wizards, the member
 *  area, the admin's dialogs and the list filters. */
export const AMOUNT_PROBLEM = 'Serve una cifra, in euro.'

/** The number an amount field holds, read the Italian way. `NaN` for anything that is not
 *  an amount, a blank field included: `Number('')` would make it 0, and `Number` alone
 *  would take «1e3» or «0x10», which the API refuses. */
export function amountNumber(value: string): number {
  const text = readAmount(value)
  return text === null ? Number.NaN : Number(text)
}

/** Whether a machine form has at most the two decimals the API keeps, trailing zeros
 *  aside: «1,500» is 1.5 and fits, «12,345» does not. */
function withinCents(text: string): boolean {
  const fraction = text.split('.')[1] ?? ''
  return fraction.replace(/0+$/, '').length <= 2
}

/** `amountNumber` for a field that sends what it holds: `NaN` too when it has more than
 *  the two decimals the API keeps («12,345»), so the field refuses it before any request. */
export function euroAmount(value: string): number {
  const text = readAmount(value)
  return text !== null && withinCents(text) ? Number(text) : Number.NaN
}

/** What every amount field sends: the checked amount with two decimals and a dot, no
 *  grouping («1.500» is "1500.00", «1,5» is "1.50"), the one form no reader, the web's or
 *  the core's, can take for another number. What `euroAmount` refuses goes as typed
 *  (trimmed), for the API to refuse by its field.
 *
 *  Every amount field sends through it: the day rate, the daily budget, the fee of a
 *  letter of engagement and the list filters. */
export function sentAmount(value: string): string {
  const number = euroAmount(value)
  return Number.isFinite(number) ? number.toFixed(2) : value.trim()
}

/** Whether a day rate or a daily budget is one the API takes: the core's `TARIFFA_MIN` to
 *  `TARIFFA_MAX`, with at most two decimals. */
export function acceptedAmount(value: string): boolean {
  const number = euroAmount(value)
  return Number.isFinite(number) && number >= 1 && number <= 99999.99
}

/** A list filter's amount in the machine form the API takes, or `undefined` when what is
 *  typed is not an amount of zero or more with at most two decimals: the filter then
 *  narrows nothing, and the field says why. */
export function amountFilter(value: string | undefined): string | undefined {
  if (value === undefined) return undefined
  const number = euroAmount(value)
  return Number.isFinite(number) && number >= 0 ? number.toFixed(2) : undefined
}
