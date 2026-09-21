/**
 * The magic-link token (`?t=`), taken out of the URL before anything else runs
 * (REB-273).
 *
 * `main.tsx` calls `stripEntraToken` as its very first statement, ahead of
 * `initAnalytics`: PostHog's `capture_pageview: 'history_change'` records the current
 * URL, including its query string, at `posthog.init` itself, so stripping the token
 * inside the `/hub/entra` route component -- after React has mounted and PostHog has
 * already captured that first pageview -- is one render too late. Taking it out here,
 * before the SPA renders at all, is what keeps the token out of that first event, out
 * of the browser's history entry, and out of any `Referer` an outbound link on the page
 * could send. Same shape as the CRM's `lib/entra-token.ts` (REB-229, PR #174); the hub
 * has one path for this route, not a per-space prefix, so the match is exact.
 *
 * The route reads the token back with `takeEntraToken`, not from its own search
 * params, since by the time it mounts the URL this module already rewrote carries
 * none. The search param stays as that read's own fallback for whatever reaches the
 * page without `main.tsx` having run first (a story a unit test can render directly).
 */
let token: string | null = null

/** Reads and clears the token, if the current URL is `/hub/entra` and carries one. A
 *  no-op everywhere else, and idempotent: a second call anywhere in the same page load
 *  finds nothing left to take. */
export function stripEntraToken(): void {
  if (typeof window === 'undefined') return
  if (window.location.pathname !== '/hub/verify') return
  const url = new URL(window.location.href)
  const t = url.searchParams.get('t')
  if (!t) return
  token = t
  url.searchParams.delete('t')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
}

/** The token `stripEntraToken` took out of the URL, consumed once: null on every call
 *  after the first, same as the URL itself once it has been rewritten. */
export function takeEntraToken(): string | null {
  const taken = token
  token = null
  return taken
}
