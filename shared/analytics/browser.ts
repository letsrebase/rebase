/**
 * How the two SPAs (the CRM and the hub) initialise PostHog, once, and the four calls
 * they make afterwards. The policy lives here so the two cannot drift: pageviews on
 * history changes (both use TanStack Router), autocapture on, session replay with every
 * input masked, anonymous visitors kept anonymous until somebody logs in, preview
 * marked as internal, and nothing at all on localhost or under a test runner.
 *
 * Every wrapper is a no-op until `initAnalytics` has decided the page is measured, so a
 * feature can call `capture` unconditionally and a test never sees a network call.
 */
import posthog, { type BeforeSendFn } from 'posthog-js'
import { POSTHOG_HOST, POSTHOG_KEY, analyticsEnabled, isInternalHost } from './posthog'

/** URL-shaped properties PostHog attaches to an event, wherever it puts them. A
 *  magic-link token (`?t=`, REB-273) has two braces already keeping it off these: the
 *  hub's and the CRM's own `lib/entra-token.ts` take it out of the browser's URL
 *  before this module even runs. This is the third, unconditional for every surface
 *  that calls `initAnalytics`. Since REB-675 the hub's team page also opens on a
 *  proposal by its id (`?proposta=`, a one-day capability to read and to file «Assumi
 *  team» on it) or on a visitor's whole project description (`?descrizione=`, free
 *  text that names companies), and neither belongs in an analytics row either: both
 *  are scrubbed here, the URL the visitor sees untouched, since a reload has to find
 *  the proposal again. `$current_url` and `$referrer` are ordinary event
 *  properties, but `$initial_current_url` (and `$initial_referrer` alongside it) is a
 *  person `$set_once` property, computed once from the very first pageview this
 *  browser ever sent PostHog and carried on every event after -- a sibling of
 *  `properties`, not a member of it, so it sits beside the person's identity once
 *  `identifyUser` runs and outlives any single page. */
const URL_PROPERTIES_WITH_TOKEN = ['$current_url', '$initial_current_url', '$referrer'] as const

/** The query parameters no analytics row may keep: the magic-link token, the team
 *  proposal's id and the project description (REB-675). */
const SCRUBBED_PARAMS = ['t', 'proposta', 'descrizione'] as const

/** The property bags a `CaptureResult` carries values in, besides its own required
 *  fields: `properties` always exists, `$set`/`$set_once` do not on every event. */
const PROPERTY_BAGS = ['properties', '$set', '$set_once'] as const

function withoutTrackingToken(url: unknown): unknown {
  if (typeof url !== 'string') return url
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return url
  }
  if (!SCRUBBED_PARAMS.some((param) => parsed.searchParams.has(param))) return url
  for (const param of SCRUBBED_PARAMS) parsed.searchParams.delete(param)
  return parsed.toString()
}

const scrubTrackingToken: BeforeSendFn = (result) => {
  if (!result) return result
  for (const bagName of PROPERTY_BAGS) {
    const bag = result[bagName]
    if (!bag) continue
    for (const key of URL_PROPERTIES_WITH_TOKEN) {
      if (key in bag) bag[key] = withoutTrackingToken(bag[key])
    }
  }
  return result
}

export interface AnalyticsOptions {
  /**
   * Mask every text node and every DOM attribute in a recording, and stop autocapture
   * from sending an element's text or attributes as event properties -- not only the
   * inputs a replay masks by default. Three separate PostHog switches, none a subset
   * of another: `session_recording.maskTextSelector` hides replay's own text nodes,
   * `session_recording.maskAllElementAttributes` hides replay's `href`/`src`/`class`
   * (a masked `mailto:` link's text without this still shows the address in its
   * `href`), and the top-level `mask_all_text`/`mask_all_element_attributes` stop
   * autocapture's click/change events from carrying `$el_text` and `attr__href` as
   * ordinary, searchable properties -- a path replay's own masking never touches.
   * Both surfaces set this now: the CRM for invoices and customer names, the hub
   * (REB-274) for the admin area's candidate and company data.
   */
  maskText?: boolean
  /** The hostname to decide on; defaults to the page's own. Tests pass one. */
  hostname?: string
}

let active = false

/** Initialises PostHog and answers whether this page is measured at all. */
export function initAnalytics(options: AnalyticsOptions = {}): boolean {
  const hostname =
    options.hostname ?? (typeof window === 'undefined' ? '' : window.location.hostname)
  if (!analyticsEnabled(hostname)) {
    active = false
    return false
  }
  try {
    posthog.init(POSTHOG_KEY, {
      api_host: POSTHOG_HOST,
      defaults: '2026-08-30',
      person_profiles: 'identified_only',
      capture_pageview: 'history_change',
      autocapture: true,
      ...(options.maskText ? { mask_all_text: true, mask_all_element_attributes: true } : {}),
      session_recording: {
        maskAllInputs: true,
        ...(options.maskText ? { maskTextSelector: '*', maskAllElementAttributes: true } : {}),
      },
      before_send: scrubTrackingToken,
    })
    // Called rather than configured: `internal_or_test_user_hostname` exists as an
    // option, but it did not take effect when tried live against array.js 1.430.2 on
    // 2026-09-12 (the running config still showed the SDK's own default for it, and the
    // events arrived unmarked). The explicit call is what the option ends up making.
    if (isInternalHost(hostname)) posthog.setInternalOrTestUser()
  } catch {
    // Both SPAs call this before their first render: a blank page is worse than no
    // analytics, so an SDK that throws leaves the page silent and rendered.
    active = false
    return false
  }
  active = true
  return true
}

/** True once `initAnalytics` decided this page sends events. */
export function analyticsActive(): boolean {
  return active
}

export function identifyUser(id: string, properties?: Record<string, unknown>): void {
  if (active) posthog.identify(id, properties)
}

export function identifyGroup(
  type: string,
  key: string,
  properties?: Record<string, unknown>,
): void {
  if (active) posthog.group(type, key, properties)
}

export function capture(event: string, properties?: Record<string, unknown>): void {
  if (active) posthog.capture(event, properties)
}

/** The id PostHog gave this browser, so a server-side event can land on the same
 *  person (`rebase_core/analytics.py`, REB-215); `null` when the page is not measured
 *  or the SDK has none to give. */
export function distinctId(): string | null {
  if (!active) return null
  try {
    return posthog.get_distinct_id() || null
  } catch {
    return null
  }
}

/** Forgets the person: called on logout, before the page leaves. */
export function resetUser(): void {
  if (active) posthog.reset()
}

/** For tests only: back to the state before `initAnalytics`. */
export function __resetAnalyticsForTests(): void {
  active = false
}
