export type ExportTab = 'prepare' | 'exports' | 'links'

/** The tabs of «Esporta per il commercialista»: the form, the stored exports and every share link
 *  of the space (REB-1047). */
export const TABS: ReadonlyArray<{ id: ExportTab; label: string }> = [
  { id: 'prepare', label: 'Prepara' },
  { id: 'exports', label: 'Esportazioni' },
  { id: 'links', label: 'Link condivisi' },
]

export type ExportSearch = { tab: ExportTab; export?: string; link?: string }

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value !== '' ? value : undefined

/**
 * `?tab=prepare|exports|links&export=<id>&link=<id>`: the tab and the open export or link are in
 * the URL, so an address reopens a panel (and on the phone its full-screen card). The export
 * belongs to «Esportazioni» and the link to «Link condivisi»; anything else lands on «Prepara»
 * with nothing open: a tab is a view, not a record.
 */
export function validateExportSearch(search: Record<string, unknown>): ExportSearch {
  const tab = TABS.find((candidate) => candidate.id === search.tab)?.id ?? 'prepare'
  const open = tab === 'exports' ? text(search.export) : undefined
  const link = tab === 'links' ? text(search.link) : undefined
  return {
    tab,
    ...(open === undefined ? {} : { export: open }),
    ...(link === undefined ? {} : { link }),
  }
}
