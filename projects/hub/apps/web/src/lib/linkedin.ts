/** The LinkedIn answer, the way people give it (ORB-203).
 *
 *  The field asks for the name after `linkedin.com/in/`, and people paste whatever
 *  their phone hands them instead: `linkedin.com/in/ada/`, `https://it.linkedin.com/
 *  in/ada?utm_source=share`. Both are read here, by the same rules as
 *  `rebase_core.linkedin.normalise_linkedin`, which checks the value again: what this
 *  accepts, the API stores the same way. `linkedinProfile` is what is sent;
 *  `linkedinFieldValue` is what the field shows. */

export const LINKEDIN_PROFILE = 'https://www.linkedin.com/in/'
/** Opens the signed-in person's own profile, where the address is to copy from. */
export const LINKEDIN_OWN_PROFILE = 'https://www.linkedin.com/in/me/'
/** The column, `LINKEDIN_URL_MAX_LENGTH`: checked here so the step says it, not the submit. */
export const LINKEDIN_MAX_LENGTH = 300

// What a profile's name is made of once decoded: letters and digits of any script, `-`
// and `_`, as Python's `[\w-]` reads it.
const NAME = /^[\p{L}\p{N}_-]+$/u
// A name typed after the prefix, possibly followed by what LinkedIn puts after it
// (`ada/`, `ada?trk=x`). No dot or colon before the first slash, so never an address.
const TYPED_NAME = /^([^./?#:\s]+)(?:[/?#].*)?$/u
// No dot in a scheme, so `linkedin.com:443/...` is not read as the scheme `linkedin.com`.
const HAS_SCHEME = /^[a-z][a-z0-9+-]*:/i
// The scheme and host of an address, to read what follows as it was written.
const AUTHORITY = /^[a-z][a-z0-9+-]*:\/\/([^/?#]*)/i
const PROFILE_PATH = /^\/in\/([^/]*)(?:\/.*)?$/
// The API refuses these on the raw value, and `URL` would silently drop tab, CR and LF.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/

/** `''` for nothing, the address to store, or `null` when it is not a LinkedIn page. */
export function linkedinProfile(input: string): string | null {
  const text = input.trim()
  if (!text) return ''
  // A browser reads a backslash as `/` and Python's `urlsplit` does not: the API refuses it.
  if (CONTROL.test(text) || text.includes('\\')) return null
  const typed = TYPED_NAME.exec(text)
  if (typed) return profileOf(typed[1] ?? '')
  const address = HAS_SCHEME.test(text) ? text : `https://${text}`
  let url: URL
  try {
    url = new URL(address)
  } catch {
    return null
  }
  const host = url.hostname.toLowerCase()
  if (!['http:', 'https:'].includes(url.protocol)) return null
  if (host !== 'linkedin.com' && !host.endsWith('.linkedin.com')) return null
  // What was written between `//` and the path is the host `URL` read, and nothing
  // else: no credentials, no port, no `https:www...` that a browser forgives and
  // `urlsplit` does not.
  const authority = AUTHORITY.exec(address)
  if (!authority || (authority[1] ?? '').toLowerCase() !== host) return null
  // The path as written, not `url.pathname`: `URL` resolves `/in/%2e%2e` to `/`, and
  // Python's `urlsplit`, which decides what is stored, does not.
  const rest = address.replace(AUTHORITY, '')
  const profile = PROFILE_PATH.exec(rest.split(/[?#]/, 1)[0] ?? '')
  if (profile) return profileOf(profile[1] ?? '')
  const stored = `https://${host}${rest}`
  return stored.length > LINKEDIN_MAX_LENGTH ? null : stored
}

function profileOf(raw: string): string | null {
  let name: string
  try {
    name = decodeURIComponent(raw)
  } catch {
    return null
  }
  if (!NAME.test(name)) return null
  const stored = LINKEDIN_PROFILE + name
  return stored.length > LINKEDIN_MAX_LENGTH ? null : stored
}

/** What the field holds after a change from `previous` to `next`: the name alone when
 *  a personal profile arrived as more than one keystroke (a paste, an autofill, a
 *  select-all replaced), so the fixed `linkedin.com/in/` in front of it reads as one
 *  address. A single keystroke is left alone: reducing `linkedin.com/in/a` to `a`
 *  mid-word would leave the rest of the address typed after a name. */
export function linkedinFieldValue(next: string, previous = ''): string {
  if (isOneKeystroke(previous, next)) return next
  const profile = linkedinProfile(next)
  return profile && profile.startsWith(LINKEDIN_PROFILE)
    ? profile.slice(LINKEDIN_PROFILE.length)
    : next
}

/** Whether the field is showing a name, which is when the prefix belongs in front. */
export function isLinkedinName(input: string): boolean {
  return !input.trim() || TYPED_NAME.test(input.trim())
}

/** One character added or removed anywhere, and nothing else changed. */
function isOneKeystroke(previous: string, next: string): boolean {
  const [short, long] = previous.length < next.length ? [previous, next] : [next, previous]
  if (long.length - short.length !== 1) return false
  let start = 0
  while (start < short.length && short[start] === long[start]) start += 1
  return short.slice(start) === long.slice(start + 1)
}
