import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The worker file itself, run against a stand-in for its global scope: what it does with
 * a page load, an API call and one of its own files, and what it stores. The decisions
 * are `sw-policy.test.ts`'s; this is the wiring around them (REB-731).
 */
const ORIGIN = 'https://pigro.example'
const SCOPE = `${ORIGIN}/studio/app`
const OFFLINE_URL = `${SCOPE}/offline.html`

type Listener = (event: never) => void

function setup() {
  const listeners = new Map<string, Listener>()
  const stored = new Map<string, Map<string, Response>>()
  const caches = {
    open: async (name: string) => {
      const store = stored.get(name) ?? new Map<string, Response>()
      stored.set(name, store)
      return {
        addAll: async (requests: Request[]) => {
          for (const request of requests) store.set(request.url, new Response(`body of ${request.url}`))
        },
        match: async (request: Request | string) =>
          store.get(typeof request === 'string' ? request : request.url)?.clone(),
      }
    },
    keys: async () => [...stored.keys()],
    delete: async (name: string) => stored.delete(name),
  }
  const sw = {
    location: { origin: ORIGIN, href: `${SCOPE}/sw.js` },
    registration: { scope: SCOPE },
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
    skipWaiting: vi.fn().mockResolvedValue(undefined),
    clients: { claim: vi.fn().mockResolvedValue(undefined) },
  }
  const fetchMock = vi.fn()
  vi.stubGlobal('self', sw)
  vi.stubGlobal('caches', caches)
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('__BUILD_ID__', 'abc')
  vi.stubGlobal('__SHELL_PATHS__', ['/app/assets/offline-A.js'])
  return { listeners, stored, sw, fetchMock }
}

/** Dispatches a `fetch` event and returns what the worker answered, or `undefined` when
 *  it left the request to the browser. */
async function dispatch(
  listeners: Map<string, Listener>,
  init: { url: string; mode?: string; method?: string },
) {
  let answer: Promise<Response> | undefined
  const event = {
    request: { url: init.url, mode: init.mode ?? 'cors', method: init.method ?? 'GET' },
    respondWith: (response: Promise<Response>) => {
      answer = response
    },
    waitUntil: () => {},
  }
  listeners.get('fetch')?.(event as never)
  return answer && (await answer)
}

describe('sw/index.ts', () => {
  beforeEach(() => {
    vi.resetModules()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  async function load() {
    const env = setup()
    await import('../sw/index')
    return env
  }

  it('precaches the offline page next to itself and the files it is drawn with, then takes over', async () => {
    const { listeners, stored, sw } = await load()
    let done: Promise<unknown> | undefined
    listeners.get('install')?.({ waitUntil: (p: Promise<unknown>) => (done = p) } as never)
    await done
    const [cache] = [...stored.entries()]
    expect(cache?.[0]).toBe(`pigrocrm-shell|${SCOPE}|abc`)
    expect([...(cache?.[1].keys() ?? [])].sort()).toEqual([`${ORIGIN}/app/assets/offline-A.js`, OFFLINE_URL])
    expect(sw.skipWaiting).toHaveBeenCalledTimes(1)
  })

  it("sends a page load to the network, past the browser's HTTP cache", async () => {
    const { listeners, fetchMock } = await load()
    fetchMock.mockResolvedValue(new Response('the page'))
    const answer = await dispatch(listeners, { url: `${SCOPE}?tab=economica`, mode: 'navigate' })
    expect(await answer?.text()).toBe('the page')
    expect(fetchMock).toHaveBeenCalledWith(expect.objectContaining({ url: `${SCOPE}?tab=economica` }), {
      cache: 'no-cache',
    })
  })

  it('answers a page load with the offline page, as a 503, when the network is gone', async () => {
    const { listeners, fetchMock } = await load()
    let done: Promise<unknown> | undefined
    listeners.get('install')?.({ waitUntil: (p: Promise<unknown>) => (done = p) } as never)
    await done
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const answer = await dispatch(listeners, { url: `${SCOPE}/customers`, mode: 'navigate' })
    expect(answer?.status).toBe(503)
    expect(await answer?.text()).toBe(`body of ${OFFLINE_URL}`)
    expect(answer?.headers.get('Cache-Control')).toBe('no-store')
  })

  it('never answers an API call, a write or another origin: they are the browser\'s', async () => {
    const { listeners, fetchMock } = await load()
    expect(await dispatch(listeners, { url: `${ORIGIN}/studio/api/customers` })).toBeUndefined()
    expect(await dispatch(listeners, { url: `${SCOPE}/customers`, mode: 'navigate', method: 'POST' })).toBeUndefined()
    expect(await dispatch(listeners, { url: 'https://eu.i.posthog.com/e' })).toBeUndefined()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('answers one of its own files from the cache, and stores nothing else while it runs', async () => {
    const { listeners, stored, fetchMock } = await load()
    let done: Promise<unknown> | undefined
    listeners.get('install')?.({ waitUntil: (p: Promise<unknown>) => (done = p) } as never)
    await done
    const before = [...(stored.values().next().value?.keys() ?? [])].sort()

    const answer = await dispatch(listeners, { url: `${ORIGIN}/app/assets/offline-A.js` })
    expect(await answer?.text()).toBe(`body of ${ORIGIN}/app/assets/offline-A.js`)
    fetchMock.mockResolvedValue(new Response('{"nome":"Rossi"}'))
    await dispatch(listeners, { url: `${ORIGIN}/studio/api/customers` })
    await dispatch(listeners, { url: `${SCOPE}`, mode: 'navigate' })

    expect([...(stored.values().next().value?.keys() ?? [])].sort()).toEqual(before)
  })

  it("deletes the caches of the previous deploy of this space on activate, and no other space's", async () => {
    const { listeners, stored } = await load()
    stored.set(`pigrocrm-shell|${SCOPE}|old`, new Map())
    stored.set(`pigrocrm-shell|${ORIGIN}/other/app|old`, new Map())
    let done: Promise<unknown> | undefined
    listeners.get('activate')?.({ waitUntil: (p: Promise<unknown>) => (done = p) } as never)
    await done
    expect([...stored.keys()].sort()).toEqual([`pigrocrm-shell|${ORIGIN}/other/app|old`])
  })
})
