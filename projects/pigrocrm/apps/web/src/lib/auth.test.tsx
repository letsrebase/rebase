import { identifyGroup, identifyUser, resetUser } from '@rebase/analytics/browser'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth, useIsAdmin } from './auth'
import { api } from './api'

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return { ...actual, api: { ...actual.api, GET: vi.fn(), POST: vi.fn() } }
})

vi.mock('@rebase/analytics/browser', () => ({
  capture: vi.fn(),
  identifyGroup: vi.fn(),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

const ADMIN = { id: 'u1', email: 'a@p.it', nome: 'Admin', ruolo: 'admin', attivo: true }
const DEMOTED = { ...ADMIN, ruolo: 'collaboratore' }

function Probe() {
  const isAdmin = useIsAdmin()
  const { login, logout } = useAuth()
  return (
    <div>
      {isAdmin ? 'admin' : 'not-admin'}
      {/* `AppShell` fires `void logout()`; here the rejection is caught so a refused
          logout is asserted on rather than reported as an unhandled one. */}
      <button onClick={() => void logout().catch(() => undefined)}>Esci</button>
      <button onClick={() => void login('a@p.it', 'pw').catch(() => undefined)}>Accedi</button>
    </div>
  )
}

function renderProbe() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  // `shouldAdvanceTime` keeps real wall-clock progress ticking the fake clock
  // forward too -- without it, @testing-library's own `findBy*`/`waitFor`
  // polling (built on `setTimeout`) never sees time pass at all and every
  // `await screen.findByText(...)` below hangs until vitest's own test
  // timeout, never react-query's `refetchInterval`.
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('AuthProvider — session freshness', () => {
  /**
   * Fix-round item 5: `useIsAdmin`/`useCanWrite` read this same cached `me`
   * value, so without a periodic refetch, a role change or deactivation made
   * from another session only reached an already-open tab on its next full
   * reload -- the tab kept rendering every admin screen, and kept letting the
   * user try admin actions the backend would refuse, for as long as they
   * stayed on the page. This proves the poll actually happens and actually
   * updates what `useIsAdmin` reports, not merely that a `refetchInterval`
   * option is present in the source.
   */
  it('re-reads the session periodically, so a demotion made elsewhere is reflected without a reload', async () => {
    vi.mocked(api.GET).mockReturnValueOnce(Promise.resolve(ok(ADMIN)))
    renderProbe()
    expect(await screen.findByText('admin')).toBeInTheDocument()

    vi.mocked(api.GET).mockReturnValueOnce(Promise.resolve(ok(DEMOTED)))
    await vi.advanceTimersByTimeAsync(30_000)

    expect(await screen.findByText('not-admin')).toBeInTheDocument()
  })

  it('does not poll before a session exists, so the login page does not hammer the endpoint pre-auth', async () => {
    vi.mocked(api.GET).mockReturnValue(
      Promise.resolve({ error: { detail: 'Autenticazione richiesta' }, response: new Response(null, { status: 401 }) } as never),
    )
    renderProbe()
    await screen.findByText('not-admin')

    await vi.advanceTimersByTimeAsync(60_000)

    // One fetch for the initial (failed, "not authenticated") load; the
    // `refetchInterval` guard (`query.state.data ? 30_000 : false`) is what
    // keeps a `null` session from being polled at all.
    expect(api.GET).toHaveBeenCalledTimes(1)
  })
})

describe('AuthProvider — publishing a session', () => {
  /**
   * REB-662. The provider asks `me` the moment it mounts, and on the login page that
   * answer is a 401 the `queryFn` turns into `null`. On a loaded box that request can
   * still be on the wire when the person has already submitted and `POST /api/auth/login`
   * has answered 200: a fetch that resolves after a manual `setQueryData` overwrites it,
   * so the late `null` threw the freshly logged-in person back to the login form, with
   * nothing asking `me` again for thirty seconds. The session a login opened has to
   * survive the answer that was already in flight when it opened.
   */
  it('keeps the session a login opened when the me request from the page load answers 401 afterwards', async () => {
    let answerMe: (value: unknown) => void = () => undefined
    const lateMe = new Promise((resolve) => {
      answerMe = resolve
    })
    vi.mocked(api.GET).mockReturnValueOnce(lateMe as never)
    vi.mocked(api.POST).mockReturnValue(Promise.resolve(ok(ADMIN)))
    renderProbe()
    expect(screen.getByText('not-admin')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Accedi' }))
    expect(await screen.findByText('admin')).toBeInTheDocument()

    // The page load's own `me`, answered only now, after the login: a visitor with no
    // session as far as that request knew.
    answerMe({ error: { detail: 'Autenticazione richiesta' }, response: new Response(null, { status: 401 }) })
    await vi.advanceTimersByTimeAsync(100)

    expect(screen.getByText('admin')).toBeInTheDocument()
  })
})

describe('AuthProvider — who PostHog sees', () => {
  it('identifies the person and the space once the session is known, and not again on every poll', async () => {
    vi.mocked(api.GET).mockImplementation(() => Promise.resolve(ok({ ...ADMIN })))
    renderProbe()
    expect(await screen.findByText('admin')).toBeInTheDocument()

    expect(identifyUser).toHaveBeenCalledTimes(1)
    expect(identifyUser).toHaveBeenCalledWith('u1', { email: 'a@p.it', nome: 'Admin', ruolo: 'admin' })
    // jsdom's page lives at `/`, which is the root installation, not a space.
    expect(identifyGroup).toHaveBeenCalledTimes(1)
    expect(identifyGroup).toHaveBeenCalledWith('spazio', 'root')

    // The thirty-second re-read answers the same person as a new object.
    await vi.advanceTimersByTimeAsync(30_000)
    expect(api.GET).toHaveBeenCalledTimes(2)
    expect(identifyUser).toHaveBeenCalledTimes(1)
    expect(identifyGroup).toHaveBeenCalledTimes(1)
  })

  it('identifies nobody while there is no session', async () => {
    vi.mocked(api.GET).mockReturnValue(
      Promise.resolve({ error: { detail: 'Autenticazione richiesta' }, response: new Response(null, { status: 401 }) } as never),
    )
    renderProbe()
    await screen.findByText('not-admin')
    expect(identifyUser).not.toHaveBeenCalled()
    expect(identifyGroup).not.toHaveBeenCalled()
    // A visitor's first `null` is not a reset: the login pageview has to merge into
    // the person they become.
    expect(resetUser).not.toHaveBeenCalled()
  })

  it('forgets the person once when the session vanishes without a logout here', async () => {
    // Another tab logged out, or the account was deactivated: the poll answers 401 and
    // `app.tsx` router-pushes to the login page without a reload, so the identified
    // id would otherwise stay in memory and own the login page's pageviews.
    vi.mocked(api.GET).mockReturnValueOnce(Promise.resolve(ok(ADMIN)))
    renderProbe()
    expect(await screen.findByText('admin')).toBeInTheDocument()

    vi.mocked(api.GET).mockReturnValue(
      Promise.resolve({ error: { detail: 'Autenticazione richiesta' }, response: new Response(null, { status: 401 }) } as never),
    )
    await vi.advanceTimersByTimeAsync(30_000)
    expect(await screen.findByText('not-admin')).toBeInTheDocument()

    expect(resetUser).toHaveBeenCalledTimes(1)
    // No session, no poll (the interval guard), and nothing left to forget either way.
    await vi.advanceTimersByTimeAsync(60_000)
    expect(resetUser).toHaveBeenCalledTimes(1)
    expect(identifyUser).toHaveBeenCalledTimes(1)
  })

  it('forgets the person on logout, before the page leaves', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { pathname: '/', assign })
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok(ADMIN)))
    vi.mocked(api.POST).mockReturnValue(Promise.resolve(ok(undefined)))
    renderProbe()
    await screen.findByText('admin')

    fireEvent.click(screen.getByRole('button', { name: 'Esci' }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith('/app/login'))
    expect(resetUser).toHaveBeenCalledTimes(1)
    expect(vi.mocked(resetUser).mock.invocationCallOrder[0]).toBeLessThan(
      assign.mock.invocationCallOrder[0] ?? 0,
    )
  })

  it('forgets the person even when the server refused the logout, as it still leaves', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { pathname: '/', assign })
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok(ADMIN)))
    // `{ error, response }` rather than a rejected promise: `unwrap` is what throws,
    // as it does against the real client.
    vi.mocked(api.POST).mockReturnValue(
      Promise.resolve({ error: { detail: 'boom' }, response: new Response(null, { status: 500 }) } as never),
    )
    renderProbe()
    await screen.findByText('admin')

    fireEvent.click(screen.getByRole('button', { name: 'Esci' }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith('/app/login'))
    expect(resetUser).toHaveBeenCalledTimes(1)
  })
})
