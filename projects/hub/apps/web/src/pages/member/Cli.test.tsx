import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { rememberCliCode } from '@/lib/returnTo'
import { Cli } from './Cli'

const PENDING = {
  user_code: 'ABCD-EFGH',
  scope: 'work_profile',
  state: 'pending',
  can_authorize: true,
  blocked_reason: null,
  expires_at: '2026-11-17T10:10:00Z',
}
const NO_CARD = 'Per inviare il tuo profilo di lavoro serve una scheda rebase. Candidati prima dal sito, poi torna qui.'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({
    getParentRoute: () => root,
    path: '/cli',
    validateSearch: (search: Record<string, unknown>): { code?: string } => ({
      code: typeof search.code === 'string' ? search.code : undefined,
    }),
    component: Cli,
  })
  const freelance = createRoute({ getParentRoute: () => root, path: '/freelance', component: () => <h1>Candidati</h1> })
  const router = createRouter({
    routeTree: root.addChildren([page, freelance]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('/hub/cli', () => {
  it('shows the code and what the CLI will be able to do, and nothing about a device', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PENDING))
    mount('/cli?code=ABCD-EFGH')

    expect(await screen.findByLabelText('Codice')).toHaveTextContent('ABCD-EFGH')
    expect(screen.getByText(/inviare il tuo profilo di lavoro, nient.altro/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Autorizza' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Annulla' })).toBeEnabled()
    expect(document.body.textContent).not.toMatch(/dispositivo|indirizzo IP/i)
  })

  it('forgets the code the login kept for it once the page is open (REB-1137)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PENDING))
    rememberCliCode('ABCD-EFGH')
    mount('/cli?code=ABCD-EFGH')
    await screen.findByLabelText('Codice')
    expect(window.localStorage.getItem('rebase.returnTo')).toBeNull()
  })

  it('asks for the code with the one in the address', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PENDING))
    mount('/cli?code=ABCD-EFGH')
    await screen.findByLabelText('Codice')
    expect(fetch.mock.calls[0]![0]).toBe('/api/hub/cli/authorizations/ABCD-EFGH')
  })

  it('authorizes on the click and tells the person to go back to the terminal', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(answer(200, PENDING))
      .mockResolvedValueOnce(answer(200, { ...PENDING, state: 'approved', can_authorize: false }))
    mount('/cli?code=ABCD-EFGH')
    await userEvent.click(await screen.findByRole('button', { name: 'Autorizza' }))

    expect(await screen.findByRole('heading', { name: 'Autorizzato' })).toBeInTheDocument()
    expect(screen.getByText(/Torna al terminale/)).toBeInTheDocument()
    expect(fetch.mock.calls[1]![0]).toBe('/api/hub/cli/authorizations/ABCD-EFGH/approve')
    expect(fetch.mock.calls[1]![1]).toMatchObject({ method: 'POST' })
  })

  it('cancels on the click and says nothing was sent', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(answer(200, PENDING))
      .mockResolvedValueOnce(answer(200, { ...PENDING, state: 'denied', can_authorize: false }))
    mount('/cli?code=ABCD-EFGH')
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla' }))

    expect(await screen.findByRole('heading', { name: 'Annullato' })).toBeInTheDocument()
    expect(screen.getByText(/Non abbiamo ricevuto nulla/)).toBeInTheDocument()
    expect(fetch.mock.calls[1]![0]).toBe('/api/hub/cli/authorizations/ABCD-EFGH/deny')
  })

  it('gives the reason and no button to somebody with no card', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { ...PENDING, can_authorize: false, blocked_reason: NO_CARD }),
    )
    mount('/cli?code=ABCD-EFGH')

    expect(await screen.findByRole('alert')).toHaveTextContent(NO_CARD)
    expect(screen.queryByRole('button', { name: 'Autorizza' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Candidati a rebase' })).toHaveAttribute('href', '/freelance')
  })

  it('says a wrong or expired code does not count, with the API sentence', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(404, { detail: 'Il codice non è valido o è scaduto.' }),
    )
    mount('/cli?code=ZZZZ-ZZZZ')

    expect(await screen.findByRole('alert')).toHaveTextContent('Il codice non è valido o è scaduto.')
    expect(screen.getByRole('heading', { name: 'Questo codice non vale' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Autorizza' })).not.toBeInTheDocument()
  })

  it('says so when too many wrong codes were typed', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(429, { detail: 'Troppi codici sbagliati. Riprova tra qualche minuto.' }),
    )
    mount('/cli?code=ZZZZ-ZZZZ')
    expect(await screen.findByRole('heading', { name: 'Troppi tentativi' })).toBeInTheDocument()
  })

  it('asks for the code when the address has none, without calling the API', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    mount('/cli')
    expect(await screen.findByRole('heading', { name: 'Manca il codice' })).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows the API sentence when authorizing fails', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(answer(200, PENDING))
      .mockResolvedValueOnce(answer(409, { detail: 'Questo codice è già stato usato.' }))
    mount('/cli?code=ABCD-EFGH')
    await userEvent.click(await screen.findByRole('button', { name: 'Autorizza' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Questo codice è già stato usato.')
  })
})
