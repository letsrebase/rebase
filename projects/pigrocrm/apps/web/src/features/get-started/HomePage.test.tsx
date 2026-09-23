/**
 * The Home of spec 2026-09-16 §4.1 (REB-222): the start page while the space is empty,
 * the dashboard afterwards, with «Completa lo spazio» over it while a step this person
 * could do is still open. The dashboard itself is stubbed: which tab requests what is
 * `DashboardPage.test.tsx`'s subject, and here the only question is which page the Home
 * chose and what it put above it.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/query'
import type { HomeSearch } from '@/features/dashboard/search'
import { HomePage } from './HomePage'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

const auth: { user: { id: string; email: string; nome: string; ruolo: string; attivo: boolean } } = {
  user: { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin', attivo: true },
}
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: auth.user, isLoading: false, login: vi.fn(), logout: vi.fn(), enterWithLink: vi.fn() }),
  useCanWrite: () => auth.user.ruolo !== 'readonly',
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
  useBlocker: vi.fn(),
}))

vi.mock('@/features/dashboard/DashboardPage', () => ({
  DashboardPage: ({ notice }: { notice?: ReactNode }) => (
    <div data-testid="dashboard">
      <h1>Home</h1>
      {notice}
    </div>
  ),
}))

const EMPTY_PAGE = { items: [], next_cursor: null }
const ONE = (id: string) => ({ items: [{ id }], next_cursor: null })
const EMPTY: Record<string, unknown> = {
  '/api/customers': EMPTY_PAGE,
  '/api/deals': EMPTY_PAGE,
  '/api/time-entries': EMPTY_PAGE,
  '/api/documents': EMPTY_PAGE,
  '/api/tokens': [],
  '/api/gmail/account': { configured: false, account: null, banner: null, banner_text: null, missing_scopes: [] },
}
const EMITTER = { ragione_sociale: 'Ada', partita_iva: '01234567890', codice_fiscale: null }

function answers(overrides: Record<string, unknown> = {}, failing: Record<string, number> = {}) {
  vi.mocked(api.GET).mockImplementation(
    ((path: string) =>
      Promise.resolve(
        path in failing
          ? { error: { detail: 'boom' }, response: new Response(null, { status: failing[path] }) }
          : path in overrides
            ? { data: overrides[path], response: new Response(null, { status: 200 }) }
            : path === '/api/emitter'
              ? { error: { detail: 'Not Found' }, response: new Response(null, { status: 404 }) }
              : { data: EMPTY[path], response: new Response(null, { status: 200 }) },
      )) as never,
  )
}

const SEARCH: HomeSearch = { tab: 'economica', da: '2026-09-01', a: '2026-09-30', base: 'competenza' }

function renderHome(search: HomeSearch = SEARCH, prepare?: (client: QueryClient) => void): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  prepare?.(client)
  render(
    <QueryClientProvider client={client}>
      <HomePage search={search} onSearchChange={vi.fn()} />
    </QueryClientProvider>,
  )
  return client
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  answers()
})

afterEach(() => {
  auth.user = { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin', attivo: true }
})

describe('the Home', () => {
  it('is the start page on an empty space, and draws no dashboard', async () => {
    renderHome()
    expect(await screen.findByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Fai lavorare l’assistente' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Oppure a mano' })).toBeInTheDocument()
    expect(screen.queryByTestId('dashboard')).toBeNull()
  })

  it('stays the start page with the fiscal data saved and a token: those are steps, not work', async () => {
    answers({ '/api/emitter': EMITTER, '/api/tokens': [{ id: 'k1' }] })
    renderHome()
    expect(await screen.findByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeInTheDocument()
    expect(screen.queryByTestId('dashboard')).toBeNull()
  })

  it('shows the Gmail outcome the consent flow came back with, next to the door', async () => {
    renderHome({ ...SEARCH, esito: 'negato' })
    expect(await screen.findByText('Autorizzazione negata: la casella non è stata collegata.')).toBeInTheDocument()
  })

  it('is the dashboard with «Completa lo spazio» once a customer exists', async () => {
    answers({ '/api/customers': ONE('c1') })
    renderHome()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Completa lo spazio' })).toBeInTheDocument()
    expect(screen.getByText(/1 di 4 primi passi fatti/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Vai ai primi passi/ })).toHaveAttribute('href', '/app/get-started')
    expect(screen.queryByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeNull()
  })

  it.each([
    ['/api/deals', 'a deal'],
    ['/api/time-entries', 'a time entry'],
    ['/api/documents', 'a document'],
  ])('is the dashboard once %s answers with a row (%s)', async (path) => {
    answers({ [path]: ONE('x1') })
    renderHome()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
  })

  it('is the dashboard alone once every step is done', async () => {
    answers({
      '/api/emitter': EMITTER,
      '/api/customers': ONE('c1'),
      '/api/deals': ONE('d1'),
      '/api/documents': ONE('o1'),
    })
    renderHome()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Completa lo spazio' })).toBeNull()
  })

  it('does not ask a collaboratore to complete the one step only an admin can do', async () => {
    auth.user.ruolo = 'collaboratore'
    answers({ '/api/customers': ONE('c1'), '/api/deals': ONE('d1'), '/api/documents': ONE('o1') })
    renderHome()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Completa lo spazio' })).toBeNull()
  })

  it('does not ask a readonly user to complete anything', async () => {
    auth.user.ruolo = 'readonly'
    answers({ '/api/customers': ONE('c1') })
    renderHome()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Completa lo spazio' })).toBeNull()
  })

  it('holds its choice for the visit: a customer the assistant creates meanwhile does not take the start page, or a token on it, away', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      data: { id: 't2', nome: 'Claude Code', prefix: 'pgc_zzzz9999', last_used_at: null, revoked_at: null, created_at: '2026-09-23T10:00:00Z', token: 'pgc_x' },
      response: new Response(null, { status: 201 }),
    } as never)
    const client = renderHome()
    await userEvent.click(await screen.findByRole('button', { name: 'Crea il token' }))
    expect(await screen.findByDisplayValue('pgc_x')).toBeInTheDocument()
    // The assistant, asked by a step's prompt, has created the first customer; the reads
    // refresh, as they do when the person comes back to the tab.
    answers({ '/api/customers': ONE('c1'), '/api/tokens': [{ id: 't2' }] })
    await client.invalidateQueries()
    await waitFor(() => expect(screen.getByText(/1 di 4/)).toBeInTheDocument())
    expect(screen.getByDisplayValue('pgc_x')).toBeInTheDocument()
    expect(screen.queryByTestId('dashboard')).toBeNull()
  })

  it('does not decide on a cached «empty» that a refetch is about to replace', async () => {
    // The first customer was just created on its own page: the four reads are cached as
    // «nothing» and invalidated, and the Home mounts while they refetch.
    answers({ '/api/customers': ONE('c1') })
    const SCOPE = { scope: 'first-steps', limit: 1 }
    renderHome(SEARCH, (client) => {
      for (const key of [
        queryKeys.customers(SCOPE),
        queryKeys.deals(SCOPE),
        queryKeys.timeEntries(SCOPE),
        queryKeys.documents(SCOPE),
      ]) {
        client.setQueryData(key, false)
      }
      void client.invalidateQueries()
    })
    expect(screen.queryByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeNull()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeNull()
  })

  it('shows the Gmail outcome on the dashboard too, if the two sides read the space differently', async () => {
    answers({ '/api/customers': ONE('c1') })
    renderHome({ ...SEARCH, esito: 'collegato' })
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    expect(screen.getByText('Casella Google collegata.')).toBeInTheDocument()
  })

  it('retries a work read the way the app does, so one dropped request does not send an empty space to the dashboard', async () => {
    let calls = 0
    const base = vi.mocked(api.GET).getMockImplementation()
    vi.mocked(api.GET).mockImplementation(((path: string, init?: unknown) => {
      if (path === '/api/customers' && calls++ === 0) {
        return Promise.resolve({ error: { detail: 'boom' }, response: new Response(null, { status: 502 }) })
      }
      return (base as (p: string, i?: unknown) => unknown)(path, init)
    }) as never)
    // The app's own client retries (`lib/query.ts`); these reads must not opt out of it.
    const client = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 0 } } })
    render(
      <QueryClientProvider client={client}>
        <HomePage search={SEARCH} onSearchChange={vi.fn()} />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeInTheDocument()
    expect(calls).toBe(2)
  })

  it('does not hold a choice made on a failed read: the next read that succeeds decides', async () => {
    answers({}, { '/api/customers': 500 })
    const client = renderHome()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    answers()
    await client.invalidateQueries()
    expect(await screen.findByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeInTheDocument()
    expect(screen.queryByTestId('dashboard')).toBeNull()
  })

  it('falls back to the dashboard when a read fails, rather than guess the space is empty', async () => {
    answers({}, { '/api/customers': 500 })
    renderHome()
    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
  })
})
