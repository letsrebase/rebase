import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { keyBytes } from '@/pwa/push'
import { PushNotifications } from './PushNotifications'

/**
 * Push on this browser (REB-738), against a fake API and a stand-in for the browser's
 * push stack: the states the section shows, and the two flows that change them.
 */
const apiMock = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }))
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: apiMock }
})

const KEY = 'BKxjwKX9e78NnatCh_yjpGXPfsen4Bde692-OV1yzBQHbglA_hFXHuOXy6RtZc0RLcmG6-snPT-KqWAA3itx70o'
const ENDPOINT = 'https://fcm.googleapis.com/fcm/send/abc'

function ok<T>(data: T) {
  return { data, response: new Response(null, { status: 200 }) }
}

interface Server {
  enabled?: boolean
  subscriptions?: Array<{ endpoint: string }>
}

function serve({ enabled = true, subscriptions = [] }: Server = {}) {
  const rows = [...subscriptions]
  apiMock.GET.mockImplementation(async (path: string) => {
    if (path === '/api/push/config') return ok({ enabled, public_key: enabled ? KEY : null })
    return ok(rows.map((row, i) => ({ id: String(i), user_agent: null, created_at: '', ...row })))
  })
  apiMock.POST.mockImplementation(async (path: string, init: { body: { endpoint: string } }) => {
    if (path === '/api/push/subscribe') rows.push({ endpoint: init.body.endpoint })
    else rows.splice(0, rows.length, ...rows.filter((row) => row.endpoint !== init.body.endpoint))
    return ok({})
  })
}

interface Browser {
  subscription: { endpoint: string; options: { applicationServerKey: ArrayBuffer | null }; toJSON: () => unknown; unsubscribe: () => Promise<boolean> } | null
  permission: NotificationPermission
  grant: NotificationPermission
}

function fakeSubscription(endpoint: string, key: ArrayBuffer | null = null) {
  return {
    endpoint,
    options: { applicationServerKey: key },
    toJSON: () => ({ endpoint, keys: { p256dh: 'P', auth: 'A' } }),
    unsubscribe: vi.fn().mockResolvedValue(true),
  }
}

function stubBrowser({ existing, permission = 'default', grant = 'granted', ios = false, standalone = false, support = true }: {
  existing?: ReturnType<typeof fakeSubscription> | null
  permission?: NotificationPermission
  grant?: NotificationPermission
  ios?: boolean
  standalone?: boolean
  support?: boolean
} = {}) {
  const state: Browser = { subscription: existing ?? null, permission, grant }
  const pushManager = {
    getSubscription: vi.fn(async () => state.subscription),
    subscribe: vi.fn(async (options: { applicationServerKey: Uint8Array<ArrayBuffer> }) => {
      state.subscription = fakeSubscription(ENDPOINT, options.applicationServerKey.buffer)
      return state.subscription
    }),
  }
  if (support) {
    vi.stubGlobal('navigator', { ...navigator, serviceWorker: { ready: Promise.resolve({ pushManager }) }, ...(ios ? { standalone } : {}) })
    vi.stubGlobal('PushManager', class {})
    vi.stubGlobal('Notification', {
      get permission() {
        return state.permission
      },
      requestPermission: vi.fn(async () => {
        state.permission = state.grant
        return state.grant
      }),
    })
  } else {
    vi.stubGlobal('navigator', { ...navigator, serviceWorker: undefined })
  }
  return { pushManager, state }
}

function renderIt() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PushNotifications />
    </QueryClientProvider>,
  )
}

describe('PushNotifications', () => {
  beforeEach(() => {
    apiMock.GET.mockReset()
    apiMock.POST.mockReset()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('offers to turn push on, and does: permission, browser subscription, then the server', async () => {
    serve()
    const { pushManager } = stubBrowser()
    renderIt()

    await userEvent.click(await screen.findByRole('button', { name: 'Attiva' }))

    expect(await screen.findByRole('button', { name: 'Disattiva' })).toBeInTheDocument()
    expect(screen.getByText(/sono attive su questo browser/)).toBeInTheDocument()
    expect(pushManager.subscribe).toHaveBeenCalledWith(
      expect.objectContaining({ userVisibleOnly: true, applicationServerKey: expect.any(Uint8Array) }),
    )
    expect(apiMock.POST).toHaveBeenCalledWith('/api/push/subscribe', {
      body: { endpoint: ENDPOINT, p256dh: 'P', auth: 'A' },
    })
  })

  it('turns push off: the server forgets the browser and the browser drops its subscription', async () => {
    const existing = fakeSubscription(ENDPOINT, keyBytes(KEY).buffer)
    serve({ subscriptions: [{ endpoint: ENDPOINT }] })
    stubBrowser({ existing, permission: 'granted' })
    renderIt()

    await userEvent.click(await screen.findByRole('button', { name: 'Disattiva' }))

    expect(await screen.findByRole('button', { name: 'Attiva' })).toBeInTheDocument()
    expect(apiMock.POST).toHaveBeenCalledWith('/api/push/unsubscribe', { body: { endpoint: ENDPOINT } })
    expect(existing.unsubscribe).toHaveBeenCalled()
  })

  it('reads a browser subscription the server does not have as off', async () => {
    // Another person signed in here, or the row went: the browser still holds a subscription.
    const existing = fakeSubscription(ENDPOINT, keyBytes(KEY).buffer)
    serve({ subscriptions: [] })
    stubBrowser({ existing, permission: 'granted' })
    renderIt()

    expect(await screen.findByRole('button', { name: 'Attiva' })).toBeInTheDocument()
  })

  it("reads a subscription made with a key the operator has since rotated as off: it is dead", async () => {
    const stale = fakeSubscription(ENDPOINT, new TextEncoder().encode('the old key').buffer as ArrayBuffer)
    serve({ subscriptions: [{ endpoint: ENDPOINT }] })
    stubBrowser({ existing: stale, permission: 'granted' })
    renderIt()

    expect(await screen.findByRole('button', { name: 'Attiva' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Disattiva' })).not.toBeInTheDocument()
  })

  it('says why not, with no button, when it cannot be turned on', async () => {
    serve({ enabled: false })
    stubBrowser()
    renderIt()
    expect(await screen.findByText('Le notifiche push non sono attive su questa installazione.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says the browser does not support it', async () => {
    serve()
    stubBrowser({ support: false })
    renderIt()
    expect(await screen.findByText('Questo browser non supporta le notifiche push.')).toBeInTheDocument()
  })

  it('asks iOS for the installed app first', async () => {
    serve()
    stubBrowser({ ios: true, standalone: false })
    renderIt()
    expect(await screen.findByText(/schermata Home/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says how to unblock a site the person blocked', async () => {
    serve()
    stubBrowser({ permission: 'denied' })
    renderIt()
    expect(await screen.findByText(/Hai bloccato le notifiche di questo sito/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says so when the person refuses the permission prompt, and stays off', async () => {
    serve()
    stubBrowser({ grant: 'denied' })
    renderIt()

    await userEvent.click(await screen.findByRole('button', { name: 'Attiva' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Il permesso per le notifiche non è stato dato.'))
    expect(apiMock.POST).not.toHaveBeenCalled()
  })

  it("drops a subscription made with a key the server no longer has before it subscribes again", async () => {
    const stale = fakeSubscription('https://fcm.googleapis.com/fcm/send/old', new TextEncoder().encode('old key').buffer as ArrayBuffer)
    serve()
    const { pushManager } = stubBrowser({ existing: stale, permission: 'granted' })
    renderIt()

    await userEvent.click(await screen.findByRole('button', { name: 'Attiva' }))

    await waitFor(() => expect(pushManager.subscribe).toHaveBeenCalled())
    expect(stale.unsubscribe).toHaveBeenCalled()
  })

  it('shows what the API said when the server refuses the subscription', async () => {
    serve()
    stubBrowser()
    apiMock.POST.mockResolvedValue({
      error: { detail: 'non è l’indirizzo di un servizio push', code: 'validation_failed' },
      response: new Response(null, { status: 422 }),
    })
    renderIt()

    await userEvent.click(await screen.findByRole('button', { name: 'Attiva' }))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })
})
