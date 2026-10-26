/**
 * The service worker's decisions, as functions of plain values so `pwa/sw-policy.test.ts`
 * can hold them without a worker (REB-731). `sw/index.ts` wires them to the events.
 */

export const SHELL_CACHE_PREFIX = 'pigrocrm-shell|'

/** One cache per space and per deploy. Every space of an origin has a worker of its own
 *  but they share one Cache Storage, so a deploy's activation in one space must not
 *  delete the cache another space's worker, not yet updated, still answers from. */
export function shellCacheName(scope: string, buildId: string): string {
  return `${SHELL_CACHE_PREFIX}${scope}|${buildId}`
}

/** Caches this worker made for an older deploy of its own scope, and only those:
 *  another space's, or another feature's (the push worker's, one day), is not this
 *  function's to delete. */
export function staleCacheNames(existing: readonly string[], current: string, scope: string): string[] {
  const mine = `${SHELL_CACHE_PREFIX}${scope}|`
  return existing.filter((name) => name.startsWith(mine) && name !== current)
}

/**
 * What the worker does with a request:
 *
 * - `navigate`: a page load. Always the network. Never stored, never answered from
 *   storage: the document is one every signed-in person gets, and the data on it is
 *   theirs. Only when the network is gone does the worker answer, with the offline page.
 * - `shell`: a file the offline page is drawn with. Answered from the precache.
 * - `network`: everything else, and the worker does not touch it. In particular no API
 *   response (`/<slug>/api/...`, always carrying a session cookie) is read or written by
 *   this worker, so there is no stale copy of anybody's data for it to serve, to the
 *   person it belongs to or to the next one on the same device.
 */
export type FetchDecision = 'navigate' | 'shell' | 'network'

export interface FetchInput {
  method: string
  mode: string
  url: URL
  origin: string
  shellPaths: ReadonlySet<string>
}

export function decideFetch({ method, mode, url, origin, shellPaths }: FetchInput): FetchDecision {
  if (method !== 'GET' || url.origin !== origin) return 'network'
  if (mode === 'navigate') return 'navigate'
  return shellPaths.has(url.pathname) ? 'shell' : 'network'
}
