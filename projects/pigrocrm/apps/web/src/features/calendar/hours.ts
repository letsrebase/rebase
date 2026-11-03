/**
 * What counts as hours somebody can log, and the one normalisation applied to them.
 *
 * A module of its own rather than a function exported from `DayPanel.tsx`: a component
 * file that also exports a helper loses fast refresh, which is the rule
 * `react-refresh/only-export-components` enforces and the reason `lib/dates.ts` exists
 * at all. It also makes this provable without rendering anything.
 */

/** The hours «giornata» fills in, and half of one. Defaults in a field, never columns:
 *  a day is eight hours on a deal and stays an ordinary time entry. */
export const FULL_DAY = '8.00'
export const HALF = '4.00'

/**
 * `value` as hours the API will accept, or `null` if it is not hours at all: a positive
 * number, at most 24, with at most two decimals.
 *
 * A guard and not the rule. `ck_time_entries_ore_range` and `TimeEntryCreate` are the
 * authority and their refusal is the sentence the panel shows; what this avoids is a
 * pointless round trip for `""`, `-1` or `otto`, and a button that looks live while it
 * cannot possibly work.
 *
 * The comma is accepted and turned into a dot: an Italian keyboard produces `7,5`, and
 * refusing that would be refusing the number for the shape of its separator. Nothing
 * else is coerced -- `8h`, `8 ore` and `otto` are not hours, and guessing at them is how
 * a typo becomes a wrong figure in a P&L.
 */
export function normaliseHours(value: string): string | null {
  const trimmed = value.trim().replace(',', '.')
  if (!/^\d{1,2}(\.\d{1,2})?$/.test(trimmed)) return null
  const parsed = Number(trimmed)
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed > 24) return null
  return trimmed
}
