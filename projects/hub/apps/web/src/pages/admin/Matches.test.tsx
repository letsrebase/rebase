import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MatchesFilters } from '@/lib/api'
import { strParam } from '@/router'
import { AdminMatches } from './Matches'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

// The project's `lib` target is ES2022 and does not declare `Promise.withResolvers`
// (Node 22 and Vitest's own runtime both support it regardless): same ambient
// augmentation `lists.test.tsx` carries for this file alone.
declare global {
  interface PromiseConstructor {
    withResolvers<T>(): {
      promise: Promise<T>
      resolve: (value: T | PromiseLike<T>) => void
      reject: (reason?: unknown) => void
    }
  }
}

/** The 300ms debounce plus a margin, in real time (same reasoning as `lists.test.tsx`). */
function settle(): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>()
  setTimeout(resolve, 500)
  return promise
}

const MATCH_A = {
  id: 'm1',
  freelancer_id: 'f1',
  freelancer_nome: 'Ada',
  freelancer_cognome: 'Lovelace',
  freelancer_email: 'ada@studio.it',
  nome_azienda: 'ACME Srl',
  figura_richiesta: 'Backend developer',
  stato: 'bozza',
  lettera_numero: '2026-001',
  lettera_stato: 'in_attesa',
  lettera_data_inizio: '1° ottobre 2026',
  lettera_data_fine: null,
  created_at: '2026-09-20T10:00:00Z',
  created_by_nome: 'Ivan',
  created_by_email: 'ivan@rebase.it',
  situazione: 'La lettera n. 2026-001 è pronta: il freelance non ha ancora ricevuto nulla.',
}

const MATCH_B = {
  ...MATCH_A,
  id: 'm2',
  freelancer_nome: 'Grace',
  freelancer_cognome: 'Hopper',
  freelancer_email: 'grace@studio.it',
  nome_azienda: 'Bianchi Srl',
  figura_richiesta: 'Designer',
  stato: 'attivo',
  lettera_numero: '2026-002',
  lettera_stato: 'firmato',
  created_at: '2026-09-10T10:00:00Z',
  situazione: 'Lettera n. 2026-002 firmata il 12 settembre 2026, dal 1° ottobre 2026.',
}

/** Mirrors `lists.test.tsx`'s own `mount`: a pathless `signedIn` id, `/admin/matches`
 *  with the same `validateSearch` shape `router.tsx` gives it, and a stub for the
 *  card's «Match e contratti» destination. */
function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const matches = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/matches',
    component: AdminMatches,
    validateSearch: (search: Record<string, unknown>): MatchesFilters => ({
      stato: strParam(search.stato),
      q: strParam(search.q),
    }),
  })
  const contratti = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: () => <p>contratti</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([matches, contratti])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

function cellUnder(row: HTMLElement, header: string): HTMLElement {
  const table = row.closest('table')!
  const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
  const index = headers.indexOf(header)
  expect(index).toBeGreaterThanOrEqual(0)
  return within(row).getAllByRole('cell')[index]!
}

afterEach(() => vi.restoreAllMocks())

describe('the Match list (REB-413)', () => {
  it('lists every match, newest first, and links the freelance name to «Match e contratti»', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 2, items: [MATCH_A, MATCH_B] }))
    mount('/admin/matches')

    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('2')
    expect(within(cellUnder(ada, 'Azienda')).getByText('ACME Srl')).toBeInTheDocument()
    expect(within(cellUnder(ada, 'Stato')).getByText('Da inviare')).toBeInTheDocument()
    expect(cellUnder(ada, 'Lettera')).toHaveTextContent('2026-001')
    expect(cellUnder(ada, 'Periodo')).toHaveTextContent('1° ottobre 2026')
    const link = within(ada).getByRole('link', { name: /Ada Lovelace/ })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/contracts$/)
  })

  it('says under each state pill where the match stands, in the core’s sentence (REB-477)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 2, items: [MATCH_A, MATCH_B] }))
    mount('/admin/matches')

    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    const stato = cellUnder(ada, 'Stato')
    expect(within(stato).getByText('Da inviare')).toBeInTheDocument()
    expect(within(stato).getByText(MATCH_A.situazione)).toBeInTheDocument()
    const grace = screen.getByText('grace@studio.it').closest('tr')!
    expect(within(cellUnder(grace, 'Stato')).getByText('Attivo')).toBeInTheDocument()
    expect(within(cellUnder(grace, 'Stato')).getByText(MATCH_B.situazione)).toBeInTheDocument()
    // The letter's column keeps its number; where the letter stands is the sentence's.
    expect(cellUnder(ada, 'Lettera')).toHaveTextContent(/^n\. 2026-001$/)
  })

  it('shows nothing, no em dash, when a match has no letter', async () => {
    const noLetter = {
      ...MATCH_A,
      id: 'm3',
      lettera_numero: null,
      lettera_stato: null,
      lettera_data_inizio: null,
      lettera_data_fine: null,
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 1, items: [noLetter] }))
    mount('/admin/matches')
    const row = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(cellUnder(row, 'Lettera')).toHaveTextContent('')
    expect(cellUnder(row, 'Periodo')).toHaveTextContent('')
    expect(screen.queryByText('—')).toBeNull()
  })

  it('filters by state through a chip, the URL and the request both carrying it', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { totale: 0, items: [] }))
    const router = mount('/admin/matches')
    await screen.findByRole('heading', { name: 'Match' })
    spy.mockClear()

    await userEvent.click(screen.getByRole('button', { name: 'Da inviare' }))

    await waitFor(() => expect(router.state.location.search).toMatchObject({ stato: 'bozza' }))
    const calls = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/api/hub/matches'))
    expect(calls.some((url) => url.includes('stato=bozza'))).toBe(true)
  })

  it('debounces the search box before it reaches the API and the URL', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [] }))
    const router = mount('/admin/matches')
    await screen.findByRole('heading', { name: 'Match' })
    spy.mockClear()

    await userEvent.type(screen.getByLabelText('Cerca'), 'ada')
    await settle()

    const calls = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/api/hub/matches'))
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('q=ada')
    expect(router.state.location.search).toMatchObject({ q: 'ada' })
  })

  it('says the table is empty with no filter active, and names the filters once one narrows it to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [] }))
    mount('/admin/matches')
    expect(await screen.findByText('Nessun match qui.')).toBeInTheDocument()
  })

  it('names the active filters when a state narrows the list to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [] }))
    mount('/admin/matches?stato=concluso')
    expect(await screen.findByText('Nessun risultato per questi filtri.')).toBeInTheDocument()
  })

  it('follows ?q= when the URL moves under the open page and never writes the old search back (Greptile 4092036048)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, { totale: 0, items: [] }))
    const router = mount('/admin/matches?q=ada')
    expect(await screen.findByLabelText('Cerca')).toHaveValue('ada')

    await act(() => router.navigate({ to: '/admin/matches', search: { q: 'grace' } }))
    await waitFor(() => expect(screen.getByLabelText('Cerca')).toHaveValue('grace'))
    await settle()
    expect(router.state.location.search).toMatchObject({ q: 'grace' })

    spy.mockClear()
    await act(async () => router.history.back())
    await waitFor(() => expect(screen.getByLabelText('Cerca')).toHaveValue('ada'))
    await settle()
    expect(router.state.location.search).toMatchObject({ q: 'ada' })
    const calls = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/api/hub/matches'))
    expect(calls.some((url) => url.includes('q=grace'))).toBe(false)
  })
})
