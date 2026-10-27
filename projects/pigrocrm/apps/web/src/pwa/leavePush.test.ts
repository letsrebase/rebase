import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { leavePush } from './leavePush'

/** Signing out takes this browser's push with it (REB-738): the server's row, then the
 *  browser's subscription, and never a logout held up or failed by either. */
const post = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({ api: { POST: post } }))

function stub(subscription: { endpoint: string; unsubscribe: () => Promise<boolean> } | null, getRegistration?: () => Promise<unknown>) {
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('navigator', {
    serviceWorker: {
      getRegistration:
        getRegistration ?? (async () => ({ pushManager: { getSubscription: async () => subscription } })),
    },
  })
}

describe('leavePush', () => {
  beforeEach(() => {
    post.mockReset().mockResolvedValue({ response: new Response(null, { status: 204 }) })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it("removes the server's row and then the browser's subscription", async () => {
    const unsubscribe = vi.fn().mockResolvedValue(true)
    stub({ endpoint: 'https://fcm.googleapis.com/fcm/send/abc', unsubscribe })

    await leavePush()

    expect(post).toHaveBeenCalledWith('/api/push/unsubscribe', {
      body: { endpoint: 'https://fcm.googleapis.com/fcm/send/abc' },
    })
    expect(unsubscribe).toHaveBeenCalled()
  })

  it('does nothing for a browser with no subscription, no worker or no push', async () => {
    stub(null)
    await leavePush()
    stub(null, async () => undefined)
    await leavePush()
    vi.stubGlobal('navigator', {})
    await leavePush()
    expect(post).not.toHaveBeenCalled()
  })

  it('never throws, whatever fails', async () => {
    post.mockRejectedValue(new TypeError('Failed to fetch'))
    stub({ endpoint: 'https://fcm.googleapis.com/fcm/send/abc', unsubscribe: vi.fn() })
    await expect(leavePush()).resolves.toBeUndefined()
  })

  it('does not hold a logout longer than a few seconds', async () => {
    vi.useFakeTimers()
    stub(null, () => new Promise(() => {}))
    const done = vi.fn()
    void leavePush().then(done)
    await vi.advanceTimersByTimeAsync(2900)
    expect(done).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(200)
    expect(done).toHaveBeenCalled()
  })
})
