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
  },
}))

import posthog, { type CaptureResult, type CapturedNetworkRequest } from 'posthog-js'
import {
  __resetAnalyticsForTests,
  analyticsActive,
  capture,
  distinctId,
  identifyGroup,
  identifyUser,
  initAnalytics,
  resetUser,
} from './browser'
import { POSTHOG_HOST, POSTHOG_KEY } from './posthog'

const init = vi.mocked(posthog.init)

beforeEach(() => {
  vi.clearAllMocks()
  __resetAnalyticsForTests()
})

afterEach(() => {
  __resetAnalyticsForTests()
})

describe('on a developer machine', () => {
  it('initialises nothing and every call is a no-op', () => {
    expect(initAnalytics({ hostname: 'localhost' })).toBe(false)
    expect(analyticsActive()).toBe(false)
    identifyUser('u1', { email: 'a@b.it' })
    identifyGroup('spazio', 'root')
    capture('cliente_creato')
    resetUser()
    expect(init).not.toHaveBeenCalled()
    expect(posthog.identify).not.toHaveBeenCalled()
    expect(posthog.group).not.toHaveBeenCalled()
    expect(posthog.capture).not.toHaveBeenCalled()
    expect(posthog.reset).not.toHaveBeenCalled()
  })
})

describe('the distinct id', () => {
  it('is nothing where nothing is measured, and the SDK\'s own id everywhere else', () => {
    expect(initAnalytics({ hostname: 'localhost' })).toBe(false)
    expect(distinctId()).toBeNull()
    __resetAnalyticsForTests()
    initAnalytics({ hostname: 'letsrebase.com' })
    expect(distinctId()).toBe('anon-1')
  })
})

describe('on a real host', () => {
  it('initialises the shared project with the shared policy', () => {
    expect(initAnalytics({ hostname: 'pigro.letsrebase.com' })).toBe(true)
    expect(analyticsActive()).toBe(true)
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

  it('marks the preview stacks as internal, so their events exist and can be filtered out', () => {
    // As a call, not as the `internal_or_test_user_hostname` option: the SDK's defaults
    // overwrote that option live (browser.ts says where and when).
    expect(initAnalytics({ hostname: 'preview.pigro.letsrebase.com' })).toBe(true)
    expect(posthog.setInternalOrTestUser).toHaveBeenCalledTimes(1)
    expect(init.mock.calls[0]?.[1]).not.toHaveProperty('internal_or_test_user_hostname')
  })

  it('masks every text node, every replay attribute and every autocapture property only when asked (REB-274)', () => {
    initAnalytics({ hostname: 'pigro.letsrebase.com', maskText: true })
    const config = init.mock.calls[0]?.[1]
    expect(config?.session_recording?.maskTextSelector).toBe('*')
    expect(config?.session_recording?.maskAllElementAttributes).toBe(true)
    expect(config?.mask_all_text).toBe(true)
    expect(config?.mask_all_element_attributes).toBe(true)
    vi.clearAllMocks()
    __resetAnalyticsForTests()
    initAnalytics({ hostname: 'letsrebase.com' })
    const withoutMaskText = init.mock.calls[0]?.[1]
    expect(withoutMaskText?.session_recording).not.toHaveProperty('maskTextSelector')
    expect(withoutMaskText?.session_recording).not.toHaveProperty('maskAllElementAttributes')
    expect(withoutMaskText).not.toHaveProperty('mask_all_text')
    expect(withoutMaskText).not.toHaveProperty('mask_all_element_attributes')
  })

  it('stays silent, and lets the page render, when the SDK throws on init', () => {
    init.mockImplementationOnce(() => {
      throw new Error('storage is not available')
    })
    expect(initAnalytics({ hostname: 'pigro.letsrebase.com' })).toBe(false)
    expect(analyticsActive()).toBe(false)
    capture('cliente_creato')
    expect(posthog.capture).not.toHaveBeenCalled()
  })

  it('passes identify, group, capture and reset through', () => {
    initAnalytics({ hostname: 'pigro.letsrebase.com' })
    identifyUser('u1', { email: 'a@b.it', nome: 'Ada' })
    identifyGroup('spazio', 'studio', { nome: 'Studio' })
    capture('cliente_creato', { via: 'ui' })
    resetUser()
    expect(posthog.identify).toHaveBeenCalledWith('u1', { email: 'a@b.it', nome: 'Ada' })
    expect(posthog.group).toHaveBeenCalledWith('spazio', 'studio', { nome: 'Studio' })
    expect(posthog.capture).toHaveBeenCalledWith('cliente_creato', { via: 'ui' })
    expect(posthog.reset).toHaveBeenCalledTimes(1)
  })
})

describe('the magic-link token scrubber', () => {
  it('removes t from $current_url and $referrer in properties, and keeps every other value', () => {
    initAnalytics({ hostname: 'letsrebase.com' })
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    const scrubbed = beforeSend({
      uuid: 'ev1',
      event: '$pageview',
      properties: {
        $current_url: 'https://letsrebase.com/hub/entra?t=abc&x=1',
        $referrer: 'https://letsrebase.com/hub/entra?t=abc&x=1',
        x: 1,
      },
    })
    expect(scrubbed?.properties.$current_url).toBe('https://letsrebase.com/hub/entra?x=1')
    expect(scrubbed?.properties.$referrer).toBe('https://letsrebase.com/hub/entra?x=1')
    expect(scrubbed?.properties.x).toBe(1)
  })

  it('removes the team page\'s proposta and descrizione too, and keeps the rest (REB-675)', () => {
    initAnalytics({ hostname: 'letsrebase.com' })
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    const url =
      'https://letsrebase.com/hub/team?proposta=5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd&descrizione=Siamo%20ACME%20e%20ci%20serve%20un%20team&da=home'
    const scrubbed = beforeSend({
      event: '$pageview',
      uuid: 'u',
      timestamp: new Date(),
      properties: { $current_url: url, $referrer: url },
      $set_once: { $initial_current_url: url },
    })
    expect(scrubbed?.properties.$current_url).toBe('https://letsrebase.com/hub/team?da=home')
    expect(scrubbed?.properties.$referrer).toBe('https://letsrebase.com/hub/team?da=home')
    expect(scrubbed?.$set_once?.$initial_current_url).toBe('https://letsrebase.com/hub/team?da=home')
  })

  it('scrubs the same parameters from what a recording keeps of a URL (REB-675)', () => {
    initAnalytics({ hostname: 'letsrebase.com' })
    const mask = init.mock.calls[0]?.[1]?.session_recording?.maskCapturedNetworkRequestFn as (
      request: CapturedNetworkRequest,
    ) => CapturedNetworkRequest
    expect(mask).toBeTypeOf('function')
    const request = {
      name: 'https://letsrebase.com/hub/team?proposta=5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd&t=abc&da=home',
      method: 'GET',
    } as CapturedNetworkRequest
    expect(mask(request)).toMatchObject({ name: 'https://letsrebase.com/hub/team?da=home', method: 'GET' })
    const clean = { name: 'https://letsrebase.com/hub/me', method: 'GET' } as CapturedNetworkRequest
    expect(mask(clean)).toBe(clean)
  })

  it('removes t from $initial_current_url in $set_once, where posthog-js actually puts it', () => {
    // Not a `properties` member: posthog-js computes it once, from the first pageview
    // this browser ever sent, and carries it as a person `$set_once` value, a sibling
    // of `properties` on the capture result.
    initAnalytics({ hostname: 'letsrebase.com' })
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    const scrubbed = beforeSend({
      uuid: 'ev1',
      event: '$pageview',
      properties: {},
      $set_once: { $initial_current_url: 'https://letsrebase.com/hub/entra?t=abc&x=1' },
    })
    expect(scrubbed?.$set_once?.$initial_current_url).toBe('https://letsrebase.com/hub/entra?x=1')
  })

  it('is a no-op on an event with none of those properties, on an event with no $set_once, and on a null result', () => {
    initAnalytics({ hostname: 'letsrebase.com' })
    const beforeSend = init.mock.calls[0]?.[1]?.before_send as (
      result: CaptureResult | null,
    ) => CaptureResult | null
    expect(beforeSend(null)).toBeNull()
    const untouched = beforeSend({ uuid: 'ev2', event: 'cliente_creato', properties: { via: 'ui' } })
    expect(untouched?.properties).toEqual({ via: 'ui' })
    expect(untouched?.$set_once).toBeUndefined()
  })
})
