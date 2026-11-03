/**
 * Names the 2026-11 English rename retired, and the things that still carry them: links
 * in mails already sent, bookmarks, a tab or a browser opened before the release.
 *
 * Two kinds, with two rules.
 *
 *  - A query parameter in a link people shared or a mail carried (`?rif=`, `?da=`,
 *    `?proposta=`, `?descrizione=`, `?persone=`, `?chi=`) is accepted forever next to
 *    its new name: the link cannot be recalled. `param` reads the new name first and
 *    falls back to the old one.
 *  - A browser storage key (`rebase.rif`, `rebase.da`, `rebase.team-risposta`,
 *    `rebase.wizard.azienda`) is read as a fallback for 30 days after the release, moved
 *    to the new name on the way, and dropped once the 30 days are over: the precedent
 *    of 2026-10-19 (`orbiters.*` in `utm.ts`). The website does the same until the same
 *    date (`projects/website/src/utm.js`).
 */

/** The end of the 30 days: 2026-12-03 (UTC), the date the website's fallback ends on. */
export const LEGACY_STORAGE_UNTIL = Date.UTC(2026, 11, 3)

/** Whether the retired storage keys are still read. `now` is for the tests. */
export function legacyStorageLive(now: number = Date.now()): boolean {
  return now < LEGACY_STORAGE_UNTIL
}

/** A query parameter by its current name, else by the name it had before the rename. */
export function param(params: URLSearchParams, current: string, legacy: string): string | null {
  return params.get(current) ?? params.get(legacy)
}

/** The same for a `Record<string, unknown>` of already parsed search params. */
export function searchParam(search: Record<string, unknown>, current: string, legacy: string): unknown {
  return search[current] ?? search[legacy]
}

/**
 * A value kept under `key`, else under the retired `legacyKey` while the 30 days last.
 * The old value is moved across (the new key written, the old one removed); once the 30
 * days are over the old key is only removed, never read. A storage that refuses is read
 * as far as it can be.
 */
export function readWithFallback(store: Storage | null, key: string, legacyKey: string, now: number = Date.now()): string | null {
  if (!store) return null
  const current = store.getItem(key)
  if (current) return current
  const old = store.getItem(legacyKey)
  if (old === null) return null
  try {
    if (legacyStorageLive(now)) store.setItem(key, old)
    store.removeItem(legacyKey)
  } catch {
    /* refused: the value is still read, from where it is */
  }
  return legacyStorageLive(now) ? old : null
}

/** The list filters, the period and the outcome of the admin pages by the names they had
 *  before the 2026-11 rename: a saved or shared `/admin/talenti?stato=nuovo` keeps its
 *  filters through the redirect to `/admin/talent`. */
const FILTER_NAMES: Record<string, string> = {
  'stato': 'status',
  'posizione': 'job_title',
  'remoto': 'remote',
  'tariffa_min': 'min_rate',
  'tariffa_max': 'max_rate',
  'pagina_origine': 'origin_page',
  'con_accessi': 'with_logins',
  'creato_da': 'created_from',
  'creato_a': 'created_to',
  'periodo_da': 'period_from',
  'origine': 'origin',
  'esito': 'outcome',
  'mese': 'month',
}

/** The values a filter had: a state, a remote option, an outcome, the whole period. */
const FILTER_VALUES: Record<string, string> = {
  'nuovo': 'new',
  'nuova': 'new',
  'contattato': 'contacted',
  'contattata': 'contacted',
  'attivo': 'active',
  'scartato': 'discarded',
  'in_corso': 'in_progress',
  'chiuso': 'closed',
  'chiusa': 'closed',
  'remoto': 'remote',
  'ibrido': 'hybrid',
  'in_sede': 'on_site',
  'tutto': 'all',
}

/** The filters whose value is one of those words (a free-text `q` is never translated). */
const VALUED_FILTERS = new Set(['status', 'remote', 'outcome', 'month'])

/** `search` with the old filter names and values given the current ones; a current name
 *  already present wins over an old one. */
export function modernSearch(search: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(search)) {
    const current = FILTER_NAMES[name] ?? name
    if (current !== name && current in search) continue
    out[current] = VALUED_FILTERS.has(current) && typeof value === 'string' ? (FILTER_VALUES[value] ?? value) : value
  }
  return out
}
