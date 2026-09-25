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

function mount(entry = '/login') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const login = createRoute({ getParentRoute: () => root, path: '/login', component: Accedi })
  const freelance = createRoute({ getParentRoute: () => root, path: '/freelance', component: () => <h1>Wizard</h1> })
  const router = createRouter({
    routeTree: root.addChildren([login, freelance]),
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
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(answer(202, { ok: true })))
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
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(answer(202, { ok: true })))
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
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(answer(202, { ok: true })))
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
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(answer(202, { ok: true })))
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
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(answer(202, { ok: true })))
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
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(503, { detail: "L'accesso via email non è ancora attivo. Riprova più avanti." }),
    )
    mount()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'ada@studio.it')
    await user.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('non è ancora attivo'),
    )
  })
})
