/**
 * The availability mail's link (`/hub/team/risposta?t=…&r=si|no`, REB-517), taken out
 * of the URL before anything else runs, the way `entra-token.ts` takes the magic link's.
 *
 * The token is one talent's answer: left in the address bar it would sit in the
 * browser's history, in the first pageview PostHog captures at `initAnalytics`, and in
 * the session replay that starts there. `main.tsx` calls `stripAnswerLink` right after
 * `stripEntraToken`, ahead of `initAnalytics`, and the page reads the pair back with
 * `takeAnswerLink`, keeping its own search params as the fallback for whatever reaches it
 * without `main.tsx` having run first (a test that renders the page directly). `r`
 * leaves with `t`: an answer without its token is nothing to keep.
 */
export interface AnswerLink {
  t: string
  r: string
}

const PATH = '/hub/team/risposta'

let link: AnswerLink | null = null

/** Reads and clears `t` and `r`, if the current URL is the answer page and carries a
 *  token. A no-op everywhere else, and idempotent: a second call finds nothing. */
export function stripAnswerLink(): void {
  if (typeof window === 'undefined') return
  if (window.location.pathname !== PATH) return
  const url = new URL(window.location.href)
  const t = url.searchParams.get('t')
  if (!t) return
  link = { t, r: url.searchParams.get('r') ?? '' }
  url.searchParams.delete('t')
  url.searchParams.delete('r')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
}

/** The pair `stripAnswerLink` took out of the URL, consumed once: `null` on every call
 *  after the first, as the URL itself is once rewritten. */
export function takeAnswerLink(): AnswerLink | null {
  const taken = link
  link = null
  return taken
}
