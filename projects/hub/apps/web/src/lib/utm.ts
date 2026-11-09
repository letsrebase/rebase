import { param, readWithFallback } from './legacy'

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

/** The six UTM keys, plus `origin`: the page of the site the person started from,
 *  `home` or `pigrocrm`, which the landing puts on its doors as `from=` (ORB-167; `da=`
 *  until the 2026-11 rename, still read: links already in the world carry it). */
export type Utm = Partial<Record<(typeof UTM_KEYS)[number] | 'origin', string>>

/** Where the landing leaves the page it was, beside the campaign. */
export const ORIGIN_STORAGE_KEY = 'rebase.from'
/** `rebase.from` was `rebase.da` until the 2026-11 rename: read for 30 days (see
 *  `legacy.ts`), moved across on the way, then dropped. */
export const PREVIOUS_ORIGIN_STORAGE_KEY = 'rebase.da'
const ORIGIN_SHAPE = /^[a-z0-9-]{1,40}$/

/** Where the landing (`projects/website/src/landing.js`) leaves the campaign for the
 *  tab, and where this app leaves what it read, so a detour through the chooser or a
 *  reload of the wizard without the query string still knows where the person came
 *  from (ORB-166). Session storage: it dies with the tab and holds only the campaign. */
export const UTM_STORAGE_KEY = 'rebase.utm'

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

/** A stored value under `key`, else the one under the name it had in the 2026-11 rename
 *  (`previousKey`, for 30 days; see `legacy.ts`). */
function recall(key: string, previousKey: string | null): string | null {
  const store = storage()
  return previousKey ? readWithFallback(store, key, previousKey) : (store?.getItem(key) ?? null)
}

/** Write `value` under `key` and drop the old name, so a tab holds one copy. */
function remember(key: string, previousKey: string | null, value: string): void {
  const store = storage()
  store?.setItem(key, value)
  if (previousKey) store?.removeItem(previousKey)
}

/** What the tab remembers, if anything readable. */
export function recallUtm(): Utm {
  const raw = recall(UTM_STORAGE_KEY, null)
  return raw ? readUtm(`?${raw}`) : {}
}

/** Remember a non-empty attribution for the tab; a storage that refuses is not an error. */
export function rememberUtm(utm: Utm): void {
  if (Object.keys(utm).length === 0) return
  try {
    remember(UTM_STORAGE_KEY, null, new URLSearchParams(utm).toString())
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

/** `from=` off a search string (`da=` in a link made before the rename), when it is a
 *  slug; anything else reads as unknown. */
export function readOrigin(search: string): string | null {
  const value = param(new URLSearchParams(search), 'from', 'da')?.trim() ?? ''
  return ORIGIN_SHAPE.test(value) ? value : null
}

/** The page the person started from: the URL's own `from=` when it has one, remembered
 *  for the tab; otherwise what the tab remembers from the landing. */
export function resolveOrigin(search: string): string | null {
  const own = readOrigin(search)
  if (own) {
    try {
      remember(ORIGIN_STORAGE_KEY, PREVIOUS_ORIGIN_STORAGE_KEY, own)
    } catch {
      /* refused: the URL still has it */
    }
    return own
  }
  const remembered = recall(ORIGIN_STORAGE_KEY, PREVIOUS_ORIGIN_STORAGE_KEY) ?? ''
  return ORIGIN_SHAPE.test(remembered) ? remembered : null
}

/** Where a member's own link leaves the code it carried, the same tab-lasting
 *  attribution `ORIGIN_STORAGE_KEY` already keeps for `from=` (P-REB-44). */
export const REFERRAL_STORAGE_KEY = 'rebase.ref'
/** `rebase.ref` was `rebase.rif` until the 2026-11 rename: read for 30 days, moved
 *  across on the way, then dropped. */
export const PREVIOUS_REFERRAL_STORAGE_KEY = 'rebase.rif'
const REFERRAL_SHAPE = /^[A-Za-z0-9]{1,10}$/

/** `ref=` off a search string, when it looks like a code; anything else is unknown.
 *  `rif=` is read as well, for good: members share `/hub/?rif=CODE` links and those
 *  cannot be recalled. */
export function readReferral(search: string): string | null {
  const value = param(new URLSearchParams(search), 'ref', 'rif')?.trim() ?? ''
  return REFERRAL_SHAPE.test(value) ? value : null
}

/** The referral code to send with an application: the URL's own `ref=` when it has
 *  one, remembered for the tab; otherwise what the tab remembers from an earlier page
 *  of this app (a detour through the chooser, say). Kept apart from `Utm`/
 *  `resolveAttribution`: a code is not ad attribution, and `Freelancer(**utm_dict)`
 *  spreads every key of that object straight onto real columns a code is not one of. */
export function resolveReferral(search: string): string | null {
  const own = readReferral(search)
  if (own) {
    try {
      remember(REFERRAL_STORAGE_KEY, PREVIOUS_REFERRAL_STORAGE_KEY, own)
    } catch {
      /* refused: the URL still has it */
    }
    return own
  }
  const remembered = recall(REFERRAL_STORAGE_KEY, PREVIOUS_REFERRAL_STORAGE_KEY) ?? ''
  return REFERRAL_SHAPE.test(remembered) ? remembered : null
}

/** Everything an application says about where it came from: the campaign and the page. */
export function resolveAttribution(search: string): Utm {
  const origin = resolveOrigin(search)
  return { ...resolveUtm(search), ...(origin ? { origin } : {}) }
}

/** What this URL says and nothing else: no campaign the tab remembers from an earlier
 *  page, and nothing remembered for a later one. For a login (REB-426), which records the
 *  link that opened the page; an application instead keeps the landing's campaign across
 *  a detour, which is `resolveAttribution`. */
export function ownAttribution(search: string): Utm {
  const origin = readOrigin(search)
  return { ...readUtm(search), ...(origin ? { origin } : {}) }
}

/** Where the login page leaves the campaign its own URL carried (REB-455), so a detour
 *  from a tracked link through the home or the area, back to a bare `/login` in the
 *  same tab, still sends it. A key of its own, written and read by the login page only:
 *  the login never reads `UTM_STORAGE_KEY` or `ORIGIN_STORAGE_KEY`, which the landing and
 *  the wizards fill, so an ad's campaign never lands on a login (REB-426's review). */
export const LOGIN_ATTRIBUTION_KEY = 'rebase.login-utm'

/** What an earlier login page of this tab remembered, read back through the same checks
 *  as a URL; an unreadable storage remembers nothing. */
function recallLoginAttribution(): Utm {
  try {
    const raw = storage()?.getItem(LOGIN_ATTRIBUTION_KEY)
    return raw ? ownAttribution(`?${raw}`) : {}
  } catch {
    return {}
  }
}

/** What a login sends, remembered for the tab. A URL with a campaign replaces what the
 *  tab remembered, its page included, since the page belonged to the older visit; a URL
 *  with only `from=` keeps the remembered campaign and takes the new page; a bare URL
 *  sends what the tab remembers. A storage that refuses is not an error. */
export function loginAttribution(search: string): Utm {
  const utm = readUtm(search)
  const origin = readOrigin(search)
  const base = Object.keys(utm).length > 0 ? utm : recallLoginAttribution()
  const attribution: Utm = origin ? { ...base, origin } : base
  const { origin: originValue, ...keys } = attribution
  const stored = new URLSearchParams(keys)
  if (originValue) stored.set('from', originValue)
  if ([...stored.keys()].length > 0) {
    try {
      storage()?.setItem(LOGIN_ATTRIBUTION_KEY, stored.toString())
    } catch {
      /* refused: this page's own URL still has it */
    }
  }
  return attribution
}

/** The same on arrival, for its effect alone: the person may leave the page before
 *  asking for the link. */
export function rememberLoginAttribution(search: string): void {
  loginAttribution(search)
}
