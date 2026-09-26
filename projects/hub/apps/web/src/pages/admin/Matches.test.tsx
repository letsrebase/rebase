import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useParams,
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
  giorni_previsti: null,
  pigro_stato: null,
  pigro_url: null,
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
 *  with the same `validateSearch` shape `router.tsx` gives it, and stubs for the
 *  card's «Match e contratti» destination and the «Pigro» column's «Consuntivo». */
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
  const report = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/matches/$id/report',
    component: function Report() {
      const { id } = useParams({ strict: false })
      return <p>{`consuntivo ${id}`}</p>
    },
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([matches, contratti, report])]),
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
    // The letter's two cells alone: «Pigro» has its own em dash for a match not active.
    expect(cellUnder(row, 'Lettera').textContent).toBe('')
    expect(cellUnder(row, 'Periodo').textContent).toBe('')
  })

  it('says in «Pigro» where each match’s link stands, and opens a linked one’s «Consuntivo» (REB-502, REB-503)', async () => {
    const url = 'https://pigro.letsrebase.com/grace/app/deal/6f1c2d3e-0000-4000-8000-000000000009'
    const linked = { ...MATCH_B, giorni_previsti: 40, pigro_stato: 'collegato', pigro_url: url }
    const states = [
      { ...MATCH_B, id: 'm4', freelancer_email: 'da-collegare@studio.it', pigro_stato: 'da_collegare' },
      { ...MATCH_B, id: 'm5', freelancer_email: 'errore@studio.it', pigro_stato: 'errore' },
      { ...MATCH_B, id: 'm6', freelancer_email: 'rifiutato@studio.it', pigro_stato: 'rifiutato' },
    ]
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 5, items: [MATCH_A, linked, ...states] }),
    )
    mount('/admin/matches')

    const grace = (await screen.findByText('grace@studio.it')).closest('tr')!
    const report = within(cellUnder(grace, 'Pigro')).getByRole('link', { name: /^Collegato/ })
    expect(report).toHaveTextContent(/^Collegato$/)
    // Spec § 3.5: the list opens the hours; the deal on Pigro is a link on that page.
    expect(report.getAttribute('href')).toMatch(/\/admin\/matches\/m2\/report$/)
    expect(report).not.toHaveAttribute('target')
    const row = (email: string) => screen.getByText(email).closest('tr')!
    expect(cellUnder(row('da-collegare@studio.it'), 'Pigro')).toHaveTextContent(/^Da collegare$/)
    expect(cellUnder(row('errore@studio.it'), 'Pigro')).toHaveTextContent(/^Errore$/)
    expect(cellUnder(row('rifiutato@studio.it'), 'Pigro')).toHaveTextContent(/^Rifiutato$/)
    expect(within(cellUnder(row('errore@studio.it'), 'Pigro')).queryByRole('link')).toBeNull()
    // A match that is not active yet has no link to speak of.
    expect(cellUnder(row('ada@studio.it'), 'Pigro')).toHaveTextContent(/^—$/)
    await userEvent.click(report)
    expect(await screen.findByText('consuntivo m2')).toBeInTheDocument()
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
