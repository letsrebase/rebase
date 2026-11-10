/**
 * How the two SPAs (the CRM and the hub) initialise PostHog, once, and the four calls
 * they make afterwards. The policy lives here so the two cannot drift: pageviews on
 * history changes (both use TanStack Router), autocapture on, session replay with every
 * input masked, anonymous visitors kept anonymous until somebody logs in, preview
 * marked as internal, and nothing at all on localhost or under a test runner.
 *
 * Every wrapper is a no-op until `initAnalytics` has decided the page is measured, so a
 * feature can call `capture` unconditionally and a test never sees a network call.
 *
 * The SDK (about 300 KB) is its own chunk, fetched by a dynamic `import()` once a page is
 * decided measured, so the page's first render never waits on it and a visitor who is
 * not measured never downloads it. A call made while the chunk is in flight is queued
 * and replayed in order when it lands.
 */
import type { BeforeSendFn, CapturedNetworkRequest } from 'posthog-js'
import { consentDecision } from './consent'
import { POSTHOG_HOST, POSTHOG_KEY, analyticsEnabled, isInternalHost } from './posthog'

/** URL-shaped properties PostHog attaches to an event, wherever it puts them. A
 *  magic-link token (`?t=`, REB-273) has two braces already keeping it off these: the
 *  hub's and the CRM's own `lib/verify-token.ts` take it out of the browser's URL
 *  before this module even runs. This is the third, unconditional for every surface
 *  that calls `initAnalytics`. Since REB-675 the hub's team page also opens on a
 *  proposal by its id (`?proposal=`, a one-day capability to read and to file «Assumi
 *  team» on it) or on a visitor's whole project description (`?description=`, free
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
 *  proposal's id and the project description (REB-675). The Italian names stay: an old
 *  link or a bookmark may still carry them into a page view. */
const SCRUBBED_PARAMS = ['t', 'proposal', 'description', 'proposta', 'descrizione'] as const

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

/** The same scrub for what a session recording keeps of a URL (REB-675): rrweb's meta
 *  event carries `location.href`, and a captured request its own, and neither is an
 *  event property `before_send` ever sees. The SDK runs this hook on both before they
 *  are stored, so the proposal id and the description leave the replay as they leave
 *  the events. */
function scrubCapturedRequest(request: CapturedNetworkRequest): CapturedNetworkRequest {
  const name = withoutTrackingToken(request.name)
  return typeof name === 'string' && name !== request.name ? { ...request, name } : request
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
  /**
   * For a surface with public routes (the hub): measure nothing until the visitor has
   * said yes to the website's cookie notice (`rebase.consent`, same origin), which that
   * notice promises ("Se dici no, non succede"). A person who signs in is the one
   * exception, the legitimate-interest case the 2026-09-12 decision records: the first
   * `identifyUser` starts the SDK, and `resetUser` (logout) stops it again unless the
   * visitor had consented. Without it the page is measured as soon as the host is.
   */
  requireConsent?: boolean
  /** The hostname to decide on; defaults to the page's own. Tests pass one. */
  hostname?: string
}

type PostHog = (typeof import('posthog-js'))['default']
type Call = (sdk: PostHog) => void

let active = false
let sdk: PostHog | null = null
let queue: Call[] = []
/** What `initAnalytics` was asked to do while it waited for a consent or a login. */
let waiting: { options: AnalyticsOptions; hostname: string } | null = null
let consentGated = false
let loading = false
/** The arguments of the last `start`, so a logout can wait for the next login with them. */
let started = { options: {} as AnalyticsOptions, hostname: '' }

function start(options: AnalyticsOptions, hostname: string): void {
  active = true
  waiting = null
  started = { options, hostname }
  if (sdk) {
    // A login after a logout on the same page: the SDK is initialised, only stopped.
    sdk.opt_in_capturing()
    return
  }
  if (loading) {
    queue.push((ph) => ph.opt_in_capturing())
    return
  }
  loading = true
  // The import is the lazy part: a page decided silent never evaluates it.
  import('posthog-js')
    .then(({ default: posthog }) => {
      posthog.init(POSTHOG_KEY, {
        api_host: POSTHOG_HOST,
        defaults: '2026-08-30',
        person_profiles: 'identified_only',
        capture_pageview: 'history_change',
        autocapture: true,
        ...(options.maskText ? { mask_all_text: true, mask_all_element_attributes: true } : {}),
        session_recording: {
          maskAllInputs: true,
          maskCapturedNetworkRequestFn: scrubCapturedRequest,
          ...(options.maskText ? { maskTextSelector: '*', maskAllElementAttributes: true } : {}),
        },
        before_send: scrubTrackingToken,
      })
      // Called rather than configured: `internal_or_test_user_hostname` exists as an
      // option, but it did not take effect when tried live against array.js 1.430.2 on
      // 2026-09-12 (the running config still showed the SDK's own default for it, and the
      // events arrived unmarked). The explicit call is what the option ends up making.
      if (isInternalHost(hostname)) posthog.setInternalOrTestUser()
      // A logout on a consent-gated page opts the browser out, and the SDK keeps that in
      // storage: the next page load (a login, or a yes given on the website since) would
      // start silenced. Whoever reaches this point has been let in, so lift it.
      if (posthog.has_opted_out_capturing()) posthog.opt_in_capturing({ captureEventName: false })
      sdk = posthog
      const pending = queue
      queue = []
      for (const call of pending) call(posthog)
    })
    .catch(() => {
      // Both SPAs call this before their first render: a blank page is worse than no
      // analytics, so an SDK that fails to load or to init leaves the page silent and
      // rendered.
      active = false
      loading = false
      sdk = null
      queue = []
    })
}

function run(call: Call): void {
  if (!active) return
  if (sdk) call(sdk)
  else queue.push(call)
}

/** Decides whether this page is measured and, if so, starts the SDK in the background.
 *  Answers whether it is measured (or will be: the SDK arrives a moment later), `false`
 *  on a silent host and while a `requireConsent` page awaits its visitor's yes. Calling
 *  it again once measured changes nothing. */
export function initAnalytics(options: AnalyticsOptions = {}): boolean {
  if (active) return true
  const hostname =
    options.hostname ?? (typeof window === 'undefined' ? '' : window.location.hostname)
  if (!analyticsEnabled(hostname)) return false
  consentGated = options.requireConsent === true && consentDecision() !== 'granted'
  if (consentGated) {
    waiting = { options, hostname }
    return false
  }
  start(options, hostname)
  return true
}

export function identifyUser(id: string, properties?: Record<string, unknown>): void {
  // Somebody signed in on a page that was waiting for a consent: see `requireConsent`.
  if (waiting) start(waiting.options, waiting.hostname)
  run((ph) => ph.identify(id, properties))
}

export function identifyGroup(
  type: string,
  key: string,
  properties?: Record<string, unknown>,
): void {
  run((ph) => ph.group(type, key, properties))
}

export function capture(event: string, properties?: Record<string, unknown>): void {
  run((ph) => ph.capture(event, properties))
}

/** The id PostHog gave this browser, so a server-side event can land on the same
 *  person (`rebase_core/analytics.py`, REB-215); `null` when the page is not measured,
 *  or the SDK has not arrived yet, or has none to give. */
export function distinctId(): string | null {
  if (!active || !sdk) return null
  try {
    return sdk.get_distinct_id() || null
  } catch {
    return null
  }
}

/** Forgets the person: called on logout, before the page leaves. On a `requireConsent`
 *  page whose visitor never said yes, it also stops measuring, so the public routes
 *  after a logout are as silent as before the login. */
export function resetUser(): void {
  if (!active) return
  run((ph) => ph.reset())
  if (!consentGated) return
  run((ph) => ph.opt_out_capturing())
  waiting = { options: started.options, hostname: started.hostname }
  active = false
}
