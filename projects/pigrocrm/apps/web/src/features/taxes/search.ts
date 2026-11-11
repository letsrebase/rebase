export type TaxesTab = 'taxes' | 'dates' | 'plans'

/** The three tabs of «Tasse» and the words they carry; «Rate» is short for 390 px (the table's
 *  own heading says «Piani di rate»). */
export const TABS: ReadonlyArray<{ id: TaxesTab; label: string }> = [
  { id: 'taxes', label: 'Imposte' },
  { id: 'dates', label: 'Scadenze' },
  { id: 'plans', label: 'Rate' },
]

export type TaxesSearch = { tab: TaxesTab; year?: number }

const FIRST_YEAR = 2000
const LAST_YEAR = 2200

/**
 * `?tab=taxes|dates|plans&year=2026`: the tab and the tax year are in the URL, so a link reopens the
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
