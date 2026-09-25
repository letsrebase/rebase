import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Accedi } from './Accedi'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Every test below drives the link request as a signed-out visitor: the page's own
 *  `useMe()` check (REB-484) must answer 401 first, or its redirect effect would fire
 *  before the assertions get a look at the form. `linkAnswer` is a factory, not a
 *  fixed `Response`, since a `Response` body can only be read once and some tests
 *  drive the link request twice. */
function fetchMock(linkAnswer: () => Response) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation((input) =>
      Promise.resolve(
        String(input) === '/api/hub/me'
          ? answer(401, { detail: 'Autenticazione richiesta' })
          : linkAnswer(),
      ),
    )
}

/** `/me` and `/admin` are stubs: this file's business is only whether the redirect
 *  effect sends a signed-in visitor to the right one, not what either page renders. */
function mount(entry = '/login') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const login = createRoute({ getParentRoute: () => root, path: '/login', component: Accedi })
  const freelance = createRoute({ getParentRoute: () => root, path: '/freelance', component: () => <h1>Wizard</h1> })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const admin = createRoute({ getParentRoute: () => root, path: '/admin', component: () => <h1>Amministrazione</h1> })
  const router = createRouter({
    routeTree: root.addChildren([login, freelance, me, admin]),
    history: createMemoryHistory({ initialEntries: [entry] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

// The page remembers a campaign for the tab (`rememberUtm`), and jsdom keeps session
// storage across the tests of one file: each test starts from a tab that saw nothing.
beforeEach(() => window.sessionStorage.clear())
afterEach(() => vi.restoreAllMocks())

describe('/login', () => {
  it('says the same thing for any address', async () => {
    // A fresh Response per call: this test drives two real submissions, and a `Response`
    // body can only be read once (`mockResolvedValue` would hand out the same instance
    // twice, which no live network round trip ever does).
    const fetchSpy = fetchMock(() => answer(202, { ok: true }))
    mount()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    const sentence = await screen.findByText(/Se sei dentro, ti abbiamo scritto/)
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/hub/auth/link',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ email: 'ada@studio.it' }) }),
    )

    await user.click(screen.getByRole('button', { name: /chiedine un altro/ }))
    await user.clear(await screen.findByLabelText('Email'))
    await user.type(screen.getByLabelText('Email'), 'nessuno@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    expect((await screen.findByText(/Se sei dentro, ti abbiamo scritto/)).textContent).toBe(
      sentence.textContent,
    )
  })

  it('sends the campaign the page was opened from with the address', async () => {
    const fetchSpy = fetchMock(() => answer(202, { ok: true }))
    const utm = 'utm_source=email&utm_medium=outreach&utm_campaign=outreach-2026-09-r2'
    mount(`/login?${utm}&utm_content=cv&utm_term=11425b70`)
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await screen.findByText(/Se sei dentro, ti abbiamo scritto/)
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/hub/auth/link',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          email: 'ada@studio.it',
          utm: {
            utm_source: 'email',
            utm_medium: 'outreach',
            utm_campaign: 'outreach-2026-09-r2',
            utm_content: 'cv',
            utm_term: '11425b70',
          },
        }),
      }),
    )
  })

  it('does not pin a campaign the tab remembers from elsewhere on a bare login', async () => {
    // A wizard opened from an ad leaves its campaign in the tab (`rememberUtm`); the
    // thanks page then links a bare /login. That login came from no campaign.
    window.sessionStorage.setItem('orbiters.utm', 'utm_source=linkedin&utm_campaign=ads')
    window.sessionStorage.setItem('orbiters.da', 'home')
    const fetchSpy = fetchMock(() => answer(202, { ok: true }))
    mount()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await screen.findByText(/Se sei dentro, ti abbiamo scritto/)
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/hub/auth/link',
      expect.objectContaining({ body: JSON.stringify({ email: 'ada@studio.it' }) }),
    )
  })

  it('sends the campaign an earlier login page of the tab arrived with', async () => {
    // The outreach case of 25/09 (REB-455): the tracked link opens /login?utm_..., the
    // person wanders to the home and the area, lands back on a bare /login and asks
    // for the link there.
    const fetchSpy = fetchMock(() => answer(202, { ok: true }))
    mount('/login?utm_source=email&utm_campaign=outreach-2026-09-r2&utm_content=scheda-vuota&utm_term=a0ff8efd')
    await screen.findByLabelText('Email')
    cleanup()
    mount('/login')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await screen.findByText(/Se sei dentro, ti abbiamo scritto/)
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/hub/auth/link',
      expect.objectContaining({
        body: JSON.stringify({
          email: 'ada@studio.it',
          utm: {
            utm_source: 'email',
            utm_campaign: 'outreach-2026-09-r2',
            utm_content: 'scheda-vuota',
            utm_term: 'a0ff8efd',
          },
        }),
      }),
    )
  })

  it('still asks for the link when the tab refuses its storage', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError')
    })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })
    const fetchSpy = fetchMock(() => answer(202, { ok: true }))
    mount('/login?utm_campaign=outreach')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await screen.findByText(/Se sei dentro, ti abbiamo scritto/)
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/hub/auth/link',
      expect.objectContaining({
        body: JSON.stringify({ email: 'ada@studio.it', utm: { utm_campaign: 'outreach' } }),
      }),
    )
  })

  it('shows the API sentence when the mail is not active yet', async () => {
    fetchMock(() => answer(503, { detail: "L'accesso via email non è ancora attivo. Riprova più avanti." }))
    mount()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('non è ancora attivo'),
    )
  })
})

describe('an already signed-in visitor (REB-484)', () => {
  // A bookmark, a shared link, Thanks.tsx's own `<Link to="/login">`, or `/hub/login`
  // reached before the marketing site's session.js ever got a chance to point the
  // click elsewhere: the request-link form has no business showing up for a session
  // that already exists.
  it('is sent to /me, and never sees the request-link form', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) =>
      Promise.resolve(
        String(input) === '/api/hub/me' ? answer(200, { role: 'member' }) : answer(404, {}),
      ),
    )
    mount()
    await screen.findByRole('heading', { name: 'La tua area' })
    expect(screen.queryByLabelText('Email')).toBeNull()
  })

  it('sends an admin to /admin instead', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) =>
      Promise.resolve(
        String(input) === '/api/hub/me' ? answer(200, { role: 'admin' }) : answer(404, {}),
      ),
    )
    mount()
    await screen.findByRole('heading', { name: 'Amministrazione' })
  })

  it('never flashes the request-link form while the session is still resolving', async () => {
    // The bookmark/shared-link/direct-hit case this whole file is about starts with no
    // `me` query already cached: `GET /api/hub/me` is genuinely in flight for a moment,
    // and the form must not render in that window either, or an already-signed-in
    // visitor sees it anyway, just briefly.
    const me = Promise.withResolvers<Response>()
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) =>
      String(input) === '/api/hub/me' ? me.promise : Promise.resolve(answer(404, {})),
    )
    mount()
    // Give React a tick to render whatever it renders while the query is pending.
    const tick = Promise.withResolvers<void>()
    setTimeout(tick.resolve, 0)
    await tick.promise
    expect(screen.queryByLabelText('Email')).toBeNull()
    me.resolve(answer(200, { role: 'member' }))
    await screen.findByRole('heading', { name: 'La tua area' })
  })
})
