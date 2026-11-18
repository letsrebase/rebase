/** Where a signed-out visitor was going, so the magic link brings them back to it (REB-1133).
 *
 *  `/hub/cli` is opened from a terminal, so the visitor is often signed out: they go through
 *  the login, open the mail's link and land on `/me`, with the code they came for lost. The
 *  page they came from is kept here, in the browser that asked for the link, and `Verify`
 *  takes it once the link is spent. Only `/cli?code=XXXX-XXXX` is ever kept or followed, and
 *  only for half an hour: a value this storage holds is never a place to send somebody to. */

const KEY = 'rebase.returnTo'
const KEEP_MS = 30 * 60 * 1000
/** The server's alphabet (`cli_auth.USER_CODE_ALPHABET`): no `0 O 1 I L`. */
const CODE = /^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$/

interface Kept {
  code: string
  at: number
}

/** Keeps the code of `/hub/cli` for the sign-in about to happen. Storage that is not there
 *  (private modes, tests) loses nothing but the convenience. */
export function rememberCliCode(typed: string, now = Date.now()): void {
  const code = typed.trim().toUpperCase()
  if (!CODE.test(code)) return
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ code, at: now } satisfies Kept))
  } catch {
    // no storage: the visitor comes back through the terminal's link
  }
}

/** The code kept by `rememberCliCode`, once: read, and forgotten. `null` when there is
 *  none, it is stale or it is not a code. */
export function takeCliCode(now = Date.now()): string | null {
  const code = peekCliCode(now)
  forgetCliCode()
  return code
}

/** The same code, left in place: the login page puts it in the link of «Accedi con GitHub»
 *  and «Accedi con Google» (REB-1137), so the provider's redirect comes back to `/hub/cli`
 *  with it; the click forgets it with `takeCliCode`. */
export function peekCliCode(now = Date.now()): string | null {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return null
    const kept = JSON.parse(raw) as Partial<Kept>
    if (typeof kept.code !== 'string' || typeof kept.at !== 'number') return null
    if (now - kept.at > KEEP_MS || !CODE.test(kept.code)) return null
    return kept.code
  } catch {
    return null
  }
}

function forgetCliCode(): void {
  try {
    window.localStorage.removeItem(KEY)
  } catch {
    // no storage: nothing was kept
  }
}
