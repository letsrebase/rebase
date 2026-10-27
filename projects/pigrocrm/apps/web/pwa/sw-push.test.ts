import { describe, expect, it, vi } from 'vitest'
import { clickTarget, notificationOptions, readMessage, registerPush } from '../sw/push'

/**
 * The push listeners (REB-738): what a message becomes on screen, where a tap goes, and
 * the wiring of both to a stand-in for the worker's global scope.
 */
const SCOPE = 'https://pigro.example/acme/app'
const ICON = '/app/assets/icons/icon-192.png'

describe('readMessage', () => {
  it('reads what the server sends', () => {
    expect(
      readMessage({
        title: 'Fattura scaduta: n. 12',
        body: 'Scaduta da 31 giorni',
        url: '/acme/app/invoices/1',
        tag: 'invoice_overdue:1',
      }),
    ).toEqual({
      title: 'Fattura scaduta: n. 12',
      body: 'Scaduta da 31 giorni',
      url: '/acme/app/invoices/1',
      tag: 'invoice_overdue:1',
    })
  })

  it.each([null, undefined, 'a string', 42, [], {}, { title: '' }, { title: '  ' }, { title: 7 }])(
    'shows nothing for %j: a push with no title is not a notification',
    (raw) => {
      expect(readMessage(raw)).toBeNull()
    },
  )

  it('drops a field that is not a string and cuts a long one', () => {
    const message = readMessage({ title: 'T'.repeat(500), body: { x: 1 }, url: 5, tag: ['a'] })
    expect(message?.title).toHaveLength(200)
    expect(message).toMatchObject({ body: undefined, url: undefined, tag: undefined })
  })
})

describe('notificationOptions', () => {
  it('tags a notification with its reminder so a climb replaces it and sounds again', () => {
    expect(
      notificationOptions({ title: 't', body: 'b', url: '/u', tag: 'k' }, ICON),
    ).toEqual({ body: 'b', icon: ICON, tag: 'k', renotify: true, data: { url: '/u' } })
  })

  it('has no tag, and so no renotify, when the message has none: it stacks', () => {
    const options = notificationOptions({ title: 't', body: undefined, url: undefined, tag: undefined }, ICON)
    expect(options).not.toHaveProperty('tag')
    expect(options).not.toHaveProperty('renotify')
  })
})

describe('clickTarget', () => {
  it('opens a path under the space', () => {
    expect(clickTarget({ url: '/acme/app/invoices/1' }, SCOPE)).toBe('https://pigro.example/acme/app/invoices/1')
    expect(clickTarget({ url: '/acme/app/activities?apri=3' }, SCOPE)).toBe(
      'https://pigro.example/acme/app/activities?apri=3',
    )
  })

  it("never leaves the origin: another one opens the space's list of reminders", () => {
    expect(clickTarget({ url: 'https://evil.example/phish' }, SCOPE)).toBe(`${SCOPE}/avvisi`)
    expect(clickTarget({ url: '//evil.example/phish' }, SCOPE)).toBe(`${SCOPE}/avvisi`)
  })

  it.each([undefined, null, {}, { url: 7 }, { url: 'http://[::bad' }])(
    'opens the list of reminders for %j',
    (data) => {
      expect(clickTarget(data, SCOPE)).toBe(`${SCOPE}/avvisi`)
    },
  )
})

type Listener = (event: never) => void

function worker(windows: Array<Record<string, unknown>> = []) {
  const listeners = new Map<string, Listener>()
  const sw = {
    registration: { scope: SCOPE, showNotification: vi.fn().mockResolvedValue(undefined) },
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
    clients: {
      matchAll: vi.fn().mockResolvedValue(windows),
      openWindow: vi.fn().mockResolvedValue(null),
    },
  }
  registerPush(sw as never, ICON)
  return { listeners, sw }
}

async function push(listeners: Map<string, Listener>, data: unknown, json = true) {
  let done: Promise<unknown> | undefined
  listeners.get('push')?.({
    data: json ? { json: () => data } : { json: () => JSON.parse('not json') },
    waitUntil: (promise: Promise<unknown>) => (done = promise),
  } as never)
  await done
  return done !== undefined
}

async function tap(listeners: Map<string, Listener>, data: unknown) {
  const close = vi.fn()
  let done: Promise<unknown> | undefined
  listeners.get('notificationclick')?.({
    notification: { close, data },
    waitUntil: (promise: Promise<unknown>) => (done = promise),
  } as never)
  await done
  return close
}

describe('registerPush', () => {
  it('shows a notification for a message', async () => {
    const { listeners, sw } = worker()
    await push(listeners, { title: 'Fattura scaduta', body: 'da 31 giorni', url: '/acme/app/invoices/1', tag: 'k' })
    expect(sw.registration.showNotification).toHaveBeenCalledWith('Fattura scaduta', {
      body: 'da 31 giorni',
      icon: ICON,
      tag: 'k',
      renotify: true,
      data: { url: '/acme/app/invoices/1' },
    })
  })

  it('shows nothing for a message that is not JSON or has no title, and does not throw', async () => {
    const { listeners, sw } = worker()
    expect(await push(listeners, null, false)).toBe(false)
    expect(await push(listeners, { body: 'no title' })).toBe(false)
    expect(sw.registration.showNotification).not.toHaveBeenCalled()
  })

  it('opens a new window on the record when none of this space is open', async () => {
    const { listeners, sw } = worker([{ url: 'https://pigro.example/other/app/x', navigate: vi.fn() }])
    const close = await tap(listeners, { url: '/acme/app/invoices/1' })
    expect(close).toHaveBeenCalled()
    expect(sw.clients.openWindow).toHaveBeenCalledWith('https://pigro.example/acme/app/invoices/1')
  })

  it('takes a window of this space already open to the record and focuses it', async () => {
    const focus = vi.fn().mockResolvedValue(undefined)
    const moved = { focus }
    const navigate = vi.fn().mockResolvedValue(moved)
    const { listeners, sw } = worker([{ url: `${SCOPE}/customers`, navigate, focus: vi.fn() }])
    await tap(listeners, { url: '/acme/app/invoices/1' })
    expect(navigate).toHaveBeenCalledWith('https://pigro.example/acme/app/invoices/1')
    expect(focus).toHaveBeenCalled()
    expect(sw.clients.openWindow).not.toHaveBeenCalled()
  })

  it('opens a window of its own when the browser will not navigate the open one', async () => {
    const navigate = vi.fn().mockRejectedValue(new TypeError('uncontrolled'))
    const { listeners, sw } = worker([{ url: `${SCOPE}/customers`, navigate, focus: vi.fn() }])
    await tap(listeners, { url: '/acme/app/invoices/1' })
    expect(sw.clients.openWindow).toHaveBeenCalledWith('https://pigro.example/acme/app/invoices/1')
  })
})
