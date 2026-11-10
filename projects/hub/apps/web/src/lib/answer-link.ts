import type { TalentAnswer } from './api'
import { readWithFallback } from './legacy'

/**
 * The availability mail's link (`/hub/team/answer?t=…&r=yes|no`; `si` in the mails sent before the rename, REB-517), taken out
 * of the URL before anything else runs, the way `verify-token.ts` takes the magic link's.
 *
 * The token is one talent's answer: left in the address bar it would sit in the
 * browser's history, in the first pageview PostHog captures at `initAnalytics`, and in
 * the session replay that starts there. `main.tsx` calls `stripAnswerLink` right after
 * `stripVerifyToken`, ahead of `initAnalytics`, and the page reads the pair back with
 * `readAnswerLink`, keeping its own search params as the fallback for whatever reaches it
 * without `main.tsx` having run first (a test that renders the page directly). `r`
 * leaves with `t`: an answer without its token is nothing to keep.
 *
 * Once out of the URL the pair is also the tab's, in session storage (REB-521): a reload
 * before «Conferma» finds a URL with no token and a module with nothing in memory, and
 * would otherwise tell the talent their link is spent when nothing was. The page calls
 * `forgetAnswerLink` once the API has answered the post. Session storage dies with the
 * tab, and nothing but this page reads it.
 */
export interface AnswerLink {
  t: string
  r: string
}

/** The page, and the path the mails sent before the 2026-11 rename still link to: both
 *  are taken out of the URL, the old one then redirects to the new. */
const PATHS = ['/hub/team/answer', '/hub/team/risposta']

/** Where the tab keeps the pair until the answer is posted. */
export const ANSWER_LINK_KEY = 'rebase.team-answer'
/** The key before the rename: read for 30 days (see `legacy.ts`), moved on the way. */
export const PREVIOUS_ANSWER_LINK_KEY = 'rebase.team-risposta'

let link: AnswerLink | null = null

/** `sessionStorage`, or `null` where reaching it throws (storage blocked). */
function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function isLink(value: unknown): value is AnswerLink {
  if (typeof value !== 'object' || value === null) return false
  const { t, r } = value as Record<string, unknown>
  return typeof t === 'string' && t !== '' && typeof r === 'string'
}

/** Reads and clears `t` and `r`, if the current URL is the answer page and carries a
 *  token, and keeps them for the tab. A no-op everywhere else, and idempotent: a second
 *  call finds nothing in the URL and leaves the pair as it is. */
export function stripAnswerLink(): void {
  if (typeof window === 'undefined') return
  if (!PATHS.includes(window.location.pathname)) return
  const url = new URL(window.location.href)
  const t = url.searchParams.get('t')
  if (!t) return
  link = { t, r: url.searchParams.get('r') ?? '' }
  try {
    storage()?.setItem(ANSWER_LINK_KEY, JSON.stringify(link))
  } catch {
    /* refused: this load still has it in memory, a reload will not */
  }
  url.searchParams.delete('t')
  url.searchParams.delete('r')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
}

/** The pair `stripAnswerLink` took out of the URL, from this load or, after a reload,
 *  from the tab; `null` when the tab never had one or it was forgotten. Not consumed:
 *  every read finds it until `forgetAnswerLink`. */
export function readAnswerLink(): AnswerLink | null {
  if (link) return link
  try {
    const raw = readWithFallback(storage(), ANSWER_LINK_KEY, PREVIOUS_ANSWER_LINK_KEY)
    if (!raw) return null
    const stored: unknown = JSON.parse(raw)
    return isLink(stored) ? { t: stored.t, r: stored.r } : null
  } catch {
    return null
  }
}

/** Drops the pair from memory and from the tab: the API has answered the post, so the
 *  token is spent, or refused, whatever a reload would show. */
export function forgetAnswerLink(): void {
  link = null
  try {
    storage()?.removeItem(ANSWER_LINK_KEY)
    storage()?.removeItem(PREVIOUS_ANSWER_LINK_KEY)
  } catch {
    /* refused: the tab forgets it when it closes */
  }
}

/** The answer a link carries: `r=yes`, or `r=si` as the mails sent before the 2026-11
 *  rename spell it; the API only ever gets `yes` or `no`. */
export function answerOf(r: string): TalentAnswer | null {
  if (r === 'yes' || r === 'si') return 'yes'
  return r === 'no' ? 'no' : null
}
