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
