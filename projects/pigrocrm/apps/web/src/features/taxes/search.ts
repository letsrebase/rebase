export type TaxesTab = 'taxes' | 'dates'

/** The two tabs of «Tasse» and the words they carry. «Rate» joins them with the plans of
 *  instalments, which have no data yet (REB-1026 leaves it to its own card). */
export const TABS: ReadonlyArray<{ id: TaxesTab; label: string }> = [
  { id: 'taxes', label: 'Imposte' },
  { id: 'dates', label: 'Scadenze' },
]

export type TaxesSearch = { tab: TaxesTab; year?: number }

const FIRST_YEAR = 2000
const LAST_YEAR = 2200

/**
 * `?tab=taxes|dates&year=2026`: the tab and the tax year are in the URL, so a link reopens the
 * same reading. Anything else opens «Imposte» on the current year. A year is whole and within
 * the range the API accepts.
 */
export function validateTaxesSearch(search: Record<string, unknown>): TaxesSearch {
  const tab = TABS.find((candidate) => candidate.id === search.tab)?.id ?? 'taxes'
  const year = search.year
  const valid =
    typeof year === 'number' && Number.isInteger(year) && year >= FIRST_YEAR && year <= LAST_YEAR
  return valid ? { tab, year } : { tab }
}
