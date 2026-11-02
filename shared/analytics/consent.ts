/**
 * The visitor's answer to the website's cookie notice, read from where `consent.js`
 * stores it. The website and the hub share the origin letsrebase.com, so a decision
 * taken there is readable here; a surface behind a login never asks (the 2026-09-12
 * decision) and a public surface starts analytics only on a `granted`.
 *
 * The key and the two values are literals `projects/website/src/consent.js` carries too
 * (the website runs without a bundler); the website's `consent.test.ts` holds them equal.
 */
export const CONSENT_STORAGE_KEY = 'rebase.consent'
/** What the key was called until REB-668, read the same way `consent.js` reads it. Gone
 *  with REB-697. */
const LEGACY_CONSENT_STORAGE_KEY = 'orbiters.consent'

export type ConsentDecision = 'granted' | 'denied'

/** The stored decision, or null when the visitor has not decided (or the browser
 *  refuses to hand storage over, which reads as no answer, never as a yes). */
export function consentDecision(): ConsentDecision | null {
  if (typeof window === 'undefined') return null
  try {
    const stored =
      window.localStorage.getItem(CONSENT_STORAGE_KEY) ??
      window.localStorage.getItem(LEGACY_CONSENT_STORAGE_KEY)
    return stored === 'granted' || stored === 'denied' ? stored : null
  } catch {
    return null
  }
}
