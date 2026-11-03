/**
 * The three display formatters the dashboard tabs share.
 *
 * Extracted rather than copied a third time. The precedent this codebase records for a
 * money formatter is a *per-feature* private helper (`features/deals/columns.tsx`), and
 * that reasoning holds for one copy: `CommercialTab.tsx` carried its own `money` and
 * `percent` and was right to. It stops holding at three. `lib/dates.ts` exists for exactly
 * this reason and says so at length -- five identical copies of a four-line date function,
 * each individually justified, and the next person to fix a bug in one had to find all
 * five. Three tabs in one folder rendering the same `Numeric(12, 2)` column is the same
 * shape, and a `percent` that drifted in one of them would print `0,00%` where a sibling
 * printed a dash for the same null.
 *
 * A plain module, not a `.tsx`: it exports no component, so there is no
 * `react-refresh/only-export-components` override to buy it -- the same reason `periodo.ts`
 * sits beside `PeriodPicker.tsx` rather than inside it.
 *
 * **Nothing here parses a number.** `no-browser-arithmetic.test.ts` scans this folder for
 * `Number()`, `parseFloat`, `parseInt` and unary `+`; `no-float-money.test.ts` scans the
 * whole of `src` for arithmetic on a named economic field. Both stay green because every
 * value below is formatted as the string the API sent.
 */

/** A `Numeric(12, 2)` column as Italian currency: the API's decimal string goes into the
 *  shared formatter verbatim, never through a float. */
export { formatMoney as money } from '@rebase/ui/format'

/**
 * A percentage, or a dash when there is none.
 *
 * A dash, not "0,00%": zero per cent means "everything I earned went out in costs", and a
 * null means nothing closed at all -- two different facts, and the one place a dashboard
 * can turn "I have no answer" into "the answer is zero". Slice 4 §7.1's rule for the
 * margin and §4's for the conversion rate are the same rule.
 *
 * `?? null` would not be enough: the field is optional in some generated types, so
 * `undefined` is reachable and must mean the same thing as `null`.
 */
export function percent(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${value.replace('.', ',')}%`
}

/**
 * A `Numeric(8, 2)` hours column with its unit, decimal comma and all.
 *
 * Not run through `Intl.NumberFormat`: these are hours, not money, and the grouping and
 * currency symbol would be wrong. A string replace of the decimal point is the whole
 * conversion, and it keeps the digits the server sent exactly as it sent them.
 */
export function hours(value: string): string {
  return `${value.replace('.', ',')} ore`
}

/** «1.50» as «1,5», «2.00» as «2»: days are quantities of a unit, not money, and a trailing
 *  «,00» on a count of days reads as a price. A string replace, never a parse. */
export function days(value: string): string {
  const trimmed = value.includes('.') ? value.replace(/\.?0+$/, '') : value
  return trimmed.replace('.', ',')
}
