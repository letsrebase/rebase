import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('posthog-js', () => ({
  default: {
    init: vi.fn(),
    identify: vi.fn(),
    group: vi.fn(),
    capture: vi.fn(),
    get_distinct_id: vi.fn(() => 'anon-1'),
    reset: vi.fn(),
    setInternalOrTestUser: vi.fn(),
    opt_in_capturing: vi.fn(),
    opt_out_capturing: vi.fn(),
    has_opted_out_capturing: vi.fn(() => false),
  },
}))

import type posthogModule from 'posthog-js'
import type { CaptureResult, CapturedNetworkRequest } from 'posthog-js'
import { POSTHOG_HOST, POSTHOG_KEY } from './posthog'

// A fresh copy of the module (and of its mocked SDK) per test: the module keeps its
// decision in module state, and the SDK arrives through a dynamic import.
let a: typeof import('./browser')
let posthog: typeof posthogModule
let init: ReturnType<typeof vi.mocked<typeof posthogModule.init>>

/** The SDK chunk has landed and the queued calls have been replayed. */
const settled = () => vi.dynamicImportSettled()

function stubBrowser(consent: string | null) {
  vi.stubGlobal('window', {
    location: { hostname: 'letsrebase.com' },
    localStorage: {
      getItem: (key: string) => (key === 'rebase.consent' ? consent : null),
    },
  })
}

beforeEach(async () => {
  vi.resetModules()
  a = await import('./browser')
  posthog = (await import('posthog-js')).default
  init = vi.mocked(posthog.init)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('on a developer machine', () => {
  it('initialises nothing and every call is a no-op', async () => {
    expect(a.initAnalytics({ hostname: 'localhost' })).toBe(false)
    await settled()
    a.identifyUser('u1', { email: 'a@b.it' })
    a.identifyGroup('space', 'root')
    a.capture('customer_created')
    a.resetUser()
    expect(init).not.toHaveBeenCalled()
    expect(posthog.identify).not.toHaveBeenCalled()
    expect(posthog.group).not.toHaveBeenCalled()
    expect(posthog.capture).not.toHaveBeenCalled()
    expect(posthog.reset).not.toHaveBeenCalled()
  })
})

describe('the distinct id', () => {
  it('is nothing where nothing is measured, and the SDK\'s own id everywhere else', async () => {
    expect(a.initAnalytics({ hostname: 'localhost' })).toBe(false)
    await settled()
    expect(a.distinctId()).toBeNull()
    a.initAnalytics({ hostname: 'letsrebase.com' })
    await settled()
    expect(a.distinctId()).toBe('anon-1')
  })
})

describe('on a real host', () => {
  it('initialises the shared project with the shared policy', async () => {
    expect(a.initAnalytics({ hostname: 'pigro.letsrebase.com' })).toBe(true)
    await settled()
    expect(init).toHaveBeenCalledTimes(1)
    const [key, config] = init.mock.calls[0] ?? []
    expect(key).toBe(POSTHOG_KEY)
    expect(config).toMatchObject({
      api_host: POSTHOG_HOST,
      person_profiles: 'identified_only',
      // TanStack Router pushes history: a SPA has to count the pages navigated to, not
      // only the full loads. Both are the 2026-08-30 defaults too; they are written out
      // so the policy reads without knowing what a date implies.
      capture_pageview: 'history_change',
      autocapture: true,
    })
    expect(config?.session_recording).toMatchObject({ maskAllInputs: true })
    // A real host is a visitor, not one of us.
    expect(posthog.setInternalOrTestUser).not.toHaveBeenCalled()
  })

  it('marks the preview stacks as internal, so their events exist and can be filtered out', async () => {
    // As a call, not as the `internal_or_test_user_hostname` option: the SDK's defaults
    // overwrote that option live (browser.ts says where and when).
    expect(a.initAnalytics({ hostname: 'preview.pigro.letsrebase.com' })).toBe(true)
    await settled()
    expect(posthog.setInternalOrTestUser).toHaveBeenCalledTimes(1)
    expect(init.mock.calls[0]?.[1]).not.toHaveProperty('internal_or_test_user_hostname')
  })

  it('masks every text node, every replay attribute and every autocapture property only when asked (REB-274)', async () => {
    a.initAnalytics({ hostname: 'pigro.letsrebase.com', maskText: true })
    await settled()
    const config = init.mock.calls[0]?.[1]
    expect(config?.session_recording?.maskTextSelector).toBe('*')
    expect(config?.session_recording?.maskAllElementAttributes).toBe(true)
    expect(config?.mask_all_text).toBe(true)
    expect(config?.mask_all_element_attributes).toBe(true)
  })

  it('leaves the text readable when not asked', async () => {
    a.initAnalytics({ hostname: 'letsrebase.com' })
    await settled()
    const withoutMaskText = init.mock.calls[0]?.[1]
    expect(withoutMaskText?.session_recording).not.toHaveProperty('maskTextSelector')
    expect(withoutMaskText?.session_recording).not.toHaveProperty('maskAllElementAttributes')
    expect(withoutMaskText).not.toHaveProperty('mask_all_text')
    expect(withoutMaskText).not.toHaveProperty('mask_all_element_attributes')
  })

  it('stays silent, and lets the page render, when the SDK throws on init', async () => {
    init.mockImplementationOnce(() => {
      throw new Error('storage is not available')
    })
    a.initAnalytics({ hostname: 'pigro.letsrebase.com' })
    a.capture('customer_created')
    await settled()
    expect(posthog.capture).not.toHaveBeenCalled()
    a.capture('customer_created')
    expect(posthog.capture).not.toHaveBeenCalled()
  })

  it('queues what is said before the SDK arrives and replays it in order', async () => {
    expect(a.initAnalytics({ hostname: 'pigro.letsrebase.com' })).toBe(true)
    a.identifyUser('u1')
    a.capture('customer_created')
    expect(posthog.capture).not.toHaveBeenCalled()
    await settled()
    expect(init).toHaveBeenCalledTimes(1)
    const order = [
      vi.mocked(posthog.identify).mock.invocationCallOrder[0],
      vi.mocked(posthog.capture).mock.invocationCallOrder[0],
    ]
    expect(order[0]).toBeLessThan(order[1] as number)
  })

  it('starts once however often it is called', async () => {
    a.initAnalytics({ hostname: 'pigro.letsrebase.com' })
    a.initAnalytics({ hostname: 'pigro.letsrebase.com' })
    await settled()
    expect(init).toHaveBeenCalledTimes(1)
  })

  it('passes identify, group, capture and reset through', async () => {
    a.initAnalytics({ hostname: 'pigro.letsrebase.com' })
    await settled()
    a.identifyUser('u1', { email: 'a@b.it', name: 'Ada' })
    a.identifyGroup('space', 'studio', { name: 'Studio' })
    a.capture('customer_created', { via: 'ui' })
    a.resetUser()
    expect(posthog.identify).toHaveBeenCalledWith('u1', { email: 'a@b.it', name: 'Ada' })
    expect(posthog.group).toHaveBeenCalledWith('space', 'studio', { name: 'Studio' })
    expect(posthog.capture).toHaveBeenCalledWith('customer_created', { via: 'ui' })
    expect(posthog.reset).toHaveBeenCalledTimes(1)
  })
})

describe('on a page with public routes (requireConsent)', () => {
  it('measures nothing until the visitor said yes on the website', async () => {
    stubBrowser(null)
    expect(a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })).toBe(false)
    a.capture('wizard_started')
    await settled()
    expect(init).not.toHaveBeenCalled()
    expect(posthog.capture).not.toHaveBeenCalled()
  })

  it('stays silent after a no, and after a browser that refuses storage', async () => {
    stubBrowser('denied')
    expect(a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })).toBe(false)
    vi.stubGlobal('window', {
      localStorage: {
        getItem: () => {
          throw new Error('blocked')
        },
      },
    })
    expect(a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })).toBe(false)
    await settled()
    expect(init).not.toHaveBeenCalled()
  })

  it('measures after a yes', async () => {
    stubBrowser('granted')
    expect(a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })).toBe(true)
    await settled()
    expect(init).toHaveBeenCalledTimes(1)
  })

  it('starts for a person who signs in, and stops again at logout', async () => {
    stubBrowser(null)
    a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })
    a.identifyUser('u1', { role: 'admin' })
    await settled()
    expect(init).toHaveBeenCalledTimes(1)
    expect(posthog.identify).toHaveBeenCalledWith('u1', { role: 'admin' })
    a.resetUser()
    expect(posthog.reset).toHaveBeenCalledTimes(1)
    expect(posthog.opt_out_capturing).toHaveBeenCalledTimes(1)
    a.capture('wizard_started')
    expect(posthog.capture).not.toHaveBeenCalled()
    a.identifyUser('u2')
    expect(posthog.opt_in_capturing).toHaveBeenCalledTimes(1)
    expect(init).toHaveBeenCalledTimes(1)
    expect(posthog.identify).toHaveBeenLastCalledWith('u2', undefined)
  })

  it('keeps measuring after logout when the visitor had said yes', async () => {
    stubBrowser('granted')
    a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })
    await settled()
    a.resetUser()
    expect(posthog.opt_out_capturing).not.toHaveBeenCalled()
    a.capture('wizard_started')
    expect(posthog.capture).toHaveBeenCalledWith('wizard_started', undefined)
  })
})

describe('an opt-out the SDK persisted from an earlier logout', () => {
  it('is lifted when a fresh load is let in, so the person is measured', async () => {
    vi.mocked(posthog.has_opted_out_capturing).mockReturnValueOnce(true)
    stubBrowser('granted')
    a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })
    await settled()
    expect(posthog.opt_in_capturing).toHaveBeenCalledWith({ captureEventName: false })
  })

  it('is left alone when there is none', async () => {
    stubBrowser('granted')
    a.initAnalytics({ hostname: 'letsrebase.com', requireConsent: true })
    await settled()
    expect(posthog.opt_in_capturing).not.toHaveBeenCalled()
  })
})

describe('the magic-link token scrubber', () => {
  it('removes t from $current_url and $referrer in properties, and keeps every other value', async () => {
    a.initAnalytics({ hostname: 'letsrebase.com' })
    await settled()
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    const scrubbed = beforeSend({
      uuid: 'ev1',
      event: '$pageview',
      properties: {
        $current_url: 'https://letsrebase.com/hub/verify?t=abc&x=1',
        $referrer: 'https://letsrebase.com/hub/verify?t=abc&x=1',
        x: 1,
      },
    })
    expect(scrubbed?.properties.$current_url).toBe('https://letsrebase.com/hub/verify?x=1')
    expect(scrubbed?.properties.$referrer).toBe('https://letsrebase.com/hub/verify?x=1')
    expect(scrubbed?.properties.x).toBe(1)
  })

  it('removes the team page\'s proposal and description too, and keeps the rest (REB-675)', async () => {
    a.initAnalytics({ hostname: 'letsrebase.com' })
    await settled()
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    const url =
      'https://letsrebase.com/hub/team?proposal=5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd&description=Siamo%20ACME%20e%20ci%20serve%20un%20team&from=home'
    const scrubbed = beforeSend({
      event: '$pageview',
      uuid: 'u',
      timestamp: new Date(),
      properties: { $current_url: url, $referrer: url },
      $set_once: { $initial_current_url: url },
    })
    expect(scrubbed?.properties.$current_url).toBe('https://letsrebase.com/hub/team?from=home')
    expect(scrubbed?.properties.$referrer).toBe('https://letsrebase.com/hub/team?from=home')
    expect(scrubbed?.$set_once?.$initial_current_url).toBe('https://letsrebase.com/hub/team?from=home')
  })

  it('scrubs the same parameters from what a recording keeps of a URL (REB-675)', async () => {
    a.initAnalytics({ hostname: 'letsrebase.com' })
    await settled()
    const mask = init.mock.calls[0]?.[1]?.session_recording?.maskCapturedNetworkRequestFn as (
      request: CapturedNetworkRequest,
    ) => CapturedNetworkRequest
    expect(mask).toBeTypeOf('function')
    const request = {
      name: 'https://letsrebase.com/hub/team?proposal=5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd&t=abc&from=home',
      method: 'GET',
    } as CapturedNetworkRequest
    expect(mask(request)).toMatchObject({ name: 'https://letsrebase.com/hub/team?from=home', method: 'GET' })
    const clean = { name: 'https://letsrebase.com/hub/me', method: 'GET' } as CapturedNetworkRequest
    expect(mask(clean)).toBe(clean)
  })

  it('removes t from $initial_current_url in $set_once, where posthog-js actually puts it', async () => {
    // Not a `properties` member: posthog-js computes it once, from the first pageview
    // this browser ever sent, and carries it as a person `$set_once` value, a sibling
    // of `properties` on the capture result.
    a.initAnalytics({ hostname: 'letsrebase.com' })
    await settled()
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    const scrubbed = beforeSend({
      uuid: 'ev1',
      event: '$pageview',
      properties: {},
      $set_once: { $initial_current_url: 'https://letsrebase.com/hub/verify?t=abc&x=1' },
    })
    expect(scrubbed?.$set_once?.$initial_current_url).toBe('https://letsrebase.com/hub/verify?x=1')
  })

  it('is a no-op on an event with none of those properties, on an event with no $set_once, and on a null result', async () => {
    a.initAnalytics({ hostname: 'letsrebase.com' })
    await settled()
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    expect(beforeSend(null)).toBeNull()
    const untouched = beforeSend({ uuid: 'ev2', event: 'customer_created', properties: { via: 'ui' } })
    expect(untouched?.properties).toEqual({ via: 'ui' })
    expect(untouched?.$set_once).toBeUndefined()
  })
})
