/**
 * Carries the signed-in identity's own email from AppShell's sidebar to the register
 * page's fast path (REB-488), without ever putting it in a URL: a `?email=` search
 * param sits in browser history and in an analytics pageview capture that only scrubs
 * `t`, the same way `POST /api/tenants/member` deliberately takes email in a body
 * rather than a query string. `sessionStorage` is same-origin and same-tab and
 * survives the full navigation `AppShell`'s `go()` does between basepaths, and it is
 * read once, at the register page's first render: a reload of that page, or a second
 * tab, never finds it again, and a URL carrying this key is not a thing that exists to
 * be crafted (Greptile, PR #425, on an earlier version that trusted a raw `?email=`).
 *
 * Reading the value back is not proof it is still this visitor's: if the sidebar's own
 * navigation never completes (the tab closes, the back button fires, before
 * register.tsx ever mounts), the value sits unread in that tab's storage until
 * something reads it -- on a kiosk or a shared machine, that could be a different
 * person much later, in the same still-open tab, after the first one logged out
 * (Greptile, PR #425, on the version with no expiry). `MAX_AGE_MS` bounds how long a
 * written handoff is honoured: short enough that a real navigation, which reads it
 * within a render, is never affected, and short enough that a later visitor to the
 * same tab finds nothing to skip ahead with.
 */
const KEY = 'pigrocrm:register-handoff-email'
const MAX_AGE_MS = 30_000

interface Handoff {
  email: string
  issuedAt: number
}

export function writeRegisterHandoffEmail(email: string): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ email, issuedAt: Date.now() } satisfies Handoff))
  } catch {
    // Storage disabled (private mode, a full quota): the normal two-step flow still
    // works, it just asks for the email again.
  }
}

export function readAndClearRegisterHandoffEmail(): string | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    sessionStorage.removeItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<Handoff>
    if (typeof parsed.email !== 'string' || typeof parsed.issuedAt !== 'number') return null
    if (Date.now() - parsed.issuedAt > MAX_AGE_MS) return null
    return parsed.email
  } catch {
    return null
  }
}
