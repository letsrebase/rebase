import { describe, expect, it } from 'vitest'
import { keyBytes, pushState, pushSupport, sameKey } from './push'

const CAN = { hasServiceWorker: true, hasPushManager: true, hasNotification: true }

describe('pushSupport', () => {
  it('is supported where the three APIs exist', () => {
    expect(pushSupport({ ...CAN, isIos: false, isStandalone: false })).toBe('supported')
  })

  it.each(['hasServiceWorker', 'hasPushManager', 'hasNotification'] as const)(
    'is unsupported without %s',
    (missing) => {
      expect(pushSupport({ ...CAN, [missing]: false, isIos: false, isStandalone: false })).toBe('unsupported')
    },
  )

  it('asks iOS for the installed app before it asks for anything else', () => {
    expect(pushSupport({ ...CAN, isIos: true, isStandalone: false })).toBe('ios-needs-install')
    expect(pushSupport({ ...CAN, isIos: true, isStandalone: true })).toBe('supported')
  })
})

describe('pushState', () => {
  const base = {
    loading: false,
    serverEnabled: true,
    support: 'supported',
    permission: 'default',
    subscribed: false,
  } as const

  it('walks the reasons in order', () => {
    expect(pushState({ ...base, loading: true })).toBe('loading')
    expect(pushState({ ...base, serverEnabled: false, support: 'unsupported' })).toBe('unavailable')
    expect(pushState({ ...base, support: 'unsupported' })).toBe('unsupported')
    expect(pushState({ ...base, support: 'ios-needs-install' })).toBe('ios-needs-install')
    expect(pushState({ ...base, permission: 'denied' })).toBe('denied')
    expect(pushState(base)).toBe('off')
    expect(pushState({ ...base, subscribed: true })).toBe('on')
  })

  it('is on for a subscription even when the permission was withdrawn since: the browser stops it', () => {
    expect(pushState({ ...base, subscribed: true, permission: 'denied' })).toBe('on')
  })
})

describe('keyBytes and sameKey', () => {
  const key = 'BKxjwKX9e78NnatCh_yjpGXPfsen4Bde692-OV1yzBQHbglA_hFXHuOXy6RtZc0RLcmG6-snPT-KqWAA3itx70o'

  it('decodes the unpadded base64url of an uncompressed P-256 point', () => {
    const bytes = keyBytes(key)
    expect(bytes).toHaveLength(65)
    expect(bytes[0]).toBe(4)
  })

  it('tells the key a subscription was made with from another', () => {
    const wanted = keyBytes(key)
    expect(sameKey(wanted.buffer, wanted)).toBe(true)
    expect(sameKey(null, wanted)).toBe(false)
    const other = keyBytes(key)
    other[10] = other[10]! ^ 1
    expect(sameKey(other.buffer, wanted)).toBe(false)
    expect(sameKey(new ArrayBuffer(3), wanted)).toBe(false)
  })
})
