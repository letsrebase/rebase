/** The attribution the page's own URL carries, if any: the five standard UTM keys and
 *  `utm_id`, which LinkedIn fills with the ad set. Read once, sent as they arrived --
 *  an unexpanded macro is stored as the literal it was, because that is what happened. */
export const UTM_KEYS = [
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_content',
  'utm_term',
  'utm_id',
] as const

/** The six UTM keys, plus `origine`: the page of the site the person started from,
 *  `home` or `pigrocrm`, which the landing puts on its doors as `da=` (ORB-167). */
export type Utm = Partial<Record<(typeof UTM_KEYS)[number] | 'origine', string>>

/** Where the landing leaves the page it was, beside the campaign. */
export const ORIGIN_STORAGE_KEY = 'orbiters.da'
const ORIGIN_SHAPE = /^[a-z0-9-]{1,40}$/

/** Where the landing (`projects/website/src/landing.js`) leaves the campaign for the
 *  tab, and where this app leaves what it read, so a detour through the chooser or a
 *  reload of the wizard without the query string still knows where the person came
 *  from (ORB-166). Session storage: it dies with the tab and holds only the campaign. */
export const UTM_STORAGE_KEY = 'orbiters.utm'

export function readUtm(search: string): Utm {
  const params = new URLSearchParams(search)
  const utm: Utm = {}
  for (const key of UTM_KEYS) {
    const value = params.get(key)?.trim().slice(0, 200)
    if (value) utm[key] = value
  }
  return utm
}

function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/** What the tab remembers, if anything readable. */
export function recallUtm(): Utm {
  const raw = storage()?.getItem(UTM_STORAGE_KEY)
  return raw ? readUtm(`?${raw}`) : {}
}

/** Remember a non-empty attribution for the tab; a storage that refuses is not an error. */
export function rememberUtm(utm: Utm): void {
  if (Object.keys(utm).length === 0) return
  try {
    storage()?.setItem(UTM_STORAGE_KEY, new URLSearchParams(utm).toString())
  } catch {
    /* refused: the URL still has it, or nothing does */
  }
}

/** The attribution to send with an application: the URL's own keys when it has any,
 *  remembered for the tab; otherwise what the tab remembers from the landing or from
 *  an earlier page of this app. */
export function resolveUtm(search: string): Utm {
  const own = readUtm(search)
  if (Object.keys(own).length > 0) {
    rememberUtm(own)
    return own
  }
  return recallUtm()
}

/** `da=` off a search string, when it is a slug; anything else reads as unknown. */
export function readOrigin(search: string): string | null {
  const value = new URLSearchParams(search).get('da')?.trim() ?? ''
  return ORIGIN_SHAPE.test(value) ? value : null
}

/** The page the person started from: the URL's own `da=` when it has one, remembered
 *  for the tab; otherwise what the tab remembers from the landing. */
export function resolveOrigin(search: string): string | null {
  const own = readOrigin(search)
  if (own) {
    try {
      storage()?.setItem(ORIGIN_STORAGE_KEY, own)
    } catch {
      /* refused: the URL still has it */
    }
    return own
  }
  const remembered = storage()?.getItem(ORIGIN_STORAGE_KEY) ?? ''
  return ORIGIN_SHAPE.test(remembered) ? remembered : null
}

/** Everything an application says about where it came from: the campaign and the page. */
export function resolveAttribution(search: string): Utm {
  const origin = resolveOrigin(search)
  return { ...resolveUtm(search), ...(origin ? { origine: origin } : {}) }
}

/** What this URL says and nothing else: no campaign the tab remembers from an earlier
 *  page, and nothing remembered for a later one. For a login (REB-426), which records the
 *  link that opened the page; an application instead keeps the landing's campaign across
 *  a detour, which is `resolveAttribution`. */
export function ownAttribution(search: string): Utm {
  const origin = readOrigin(search)
  return { ...readUtm(search), ...(origin ? { origine: origin } : {}) }
}
