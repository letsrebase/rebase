export type ExportTab = 'prepare' | 'exports'

/** The tabs of «Esporta per il commercialista». «Link condivisi» joins them with the share link
 *  (its own card, REB-1028 draws only what the server can already answer). */
export const TABS: ReadonlyArray<{ id: ExportTab; label: string }> = [
  { id: 'prepare', label: 'Prepara' },
  { id: 'exports', label: 'Esportazioni' },
]

export type ExportSearch = { tab: ExportTab; export?: string }

/**
 * `?tab=prepare|exports&export=<id>`: the tab and the open export are in the URL, so a link
 * reopens an export's panel (and on the phone its full-screen card). Anything else lands on
 * «Prepara» with nothing open: a tab is a view, not a record.
 */
export function validateExportSearch(search: Record<string, unknown>): ExportSearch {
  const tab = TABS.find((candidate) => candidate.id === search.tab)?.id ?? 'prepare'
  const open = typeof search.export === 'string' && search.export !== '' ? search.export : undefined
  return open === undefined ? { tab } : { tab, export: open }
}
