import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TeamProposalsFilters } from '@/lib/api'
import { strParam } from '@/router'
import { AdminProposteTeam } from './ProposteTeam'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const FILED = {
  id: 'p1',
  descrizione: 'Rifacciamo il gestionale degli ordini: backend in Python, sei mesi, da remoto.',
  persone: 2,
  nota: null,
  previous_id: null,
  origine: 'pubblico',
  user_id: null,
  errore: null,
  riassunto: 'Una fintech vuole una web app per i clienti: dashboard, pagamenti e open banking.',
  membri: 2,
  request_id: 'r1',
  created_at: '2026-09-29T10:00:00Z',
}

const NEVER_FILED = {
  ...FILED,
  id: 'p2',
  descrizione: 'Una pipeline dati su Google Cloud, in sede a Milano, quattro mesi.',
  persone: 1,
  nota: 'togli il designer',
  previous_id: 'p0',
  origine: 'cloud',
  user_id: 'u1',
  membri: 1,
  request_id: null,
  created_at: '2026-09-28T10:00:00Z',
}

const FAILED = {
  ...FILED,
  id: 'p3',
  descrizione: 'Un’app mobile per prenotare le lezioni in palestra, tre mesi, da remoto.',
  persone: null,
  origine: 'admin',
  errore: 'llm_unavailable',
  riassunto: null,
  membri: 0,
  request_id: null,
  created_at: '2026-09-27T10:00:00Z',
}

/** `RichiesteTeam.test.tsx`'s own `mount`: the pathless `signedIn` id,
 *  `/admin/team/proposte` with the `validateSearch` `router.tsx` gives it, and stubs
 *  for the requests list and a request's page. */
function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const list = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/team/proposte',
    component: AdminProposteTeam,
    validateSearch: (search: Record<string, unknown>): TeamProposalsFilters => ({
      esito: strParam(search.esito),
    }),
  })
  const requests = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/team',
    component: () => <p>richieste</p>,
  })
  const page = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/team/$id',
    component: () => <p>richiesta</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([list, requests, page])]),
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

function rowOf(text: string): HTMLElement {
  return screen.getByText(text).closest('tr')!
}

afterEach(() => vi.restoreAllMocks())

describe('«Proposte» (0028): every «Proponi il team», filed or not', () => {
  it('lists each ask with when, who, what, how many and what came of it', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { items: [FILED, NEVER_FILED, FAILED], next_cursor: null }),
    )
    mount('/admin/team/proposte')

    expect(await screen.findByRole('heading', { level: 1, name: 'Proposte' })).toBeInTheDocument()
    const filed = (await screen.findByText(FILED.descrizione)).closest('tr')!
    expect(cellUnder(filed, 'Origine')).toHaveTextContent('Pubblico')
    expect(cellUnder(filed, 'Persone')).toHaveTextContent('2')
    expect(cellUnder(filed, 'Esito')).toHaveTextContent('2 persone proposte')
    expect(cellUnder(filed, 'Esito')).toHaveTextContent(FILED.riassunto)
    const link = within(cellUnder(filed, 'Esito')).getByRole('link', { name: 'Richiesta inviata' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/team\/r1$/)

    const never = rowOf(NEVER_FILED.descrizione)
    expect(cellUnder(never, 'Origine')).toHaveTextContent('Cloud')
    expect(cellUnder(never, 'Descrizione')).toHaveTextContent('Rigenera: togli il designer')
    expect(cellUnder(never, 'Esito')).toHaveTextContent('1 persona proposta')
    expect(cellUnder(never, 'Esito')).toHaveTextContent('Nessuna richiesta')
    expect(within(never).queryByRole('link')).toBeNull()

    const failed = rowOf(FAILED.descrizione)
    expect(cellUnder(failed, 'Origine')).toHaveTextContent('Admin')
    expect(cellUnder(failed, 'Persone')).toHaveTextContent('—')
    expect(cellUnder(failed, 'Esito')).toHaveTextContent('Fallita: Claude non ha risposto')

    expect(spy).toHaveBeenCalledWith('/api/hub/team/proposals', expect.anything())
    expect(screen.getByRole('link', { name: 'Tutte le richieste' }).getAttribute('href')).toMatch(/\/admin\/team$/)
  })

  it('cuts a long description and a long summary, each shown whole on its own «Mostra tutto»', async () => {
    const long = `${'Un progetto lungo. '.repeat(20)}FINE`
    const summary = `${'Un riassunto lungo. '.repeat(20)}FINE`
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { items: [{ ...FILED, descrizione: long, riassunto: summary }], next_cursor: null }),
    )
    mount('/admin/team/proposte')

    const more = await screen.findAllByRole('button', { name: 'Mostra tutto' })
    expect(more).toHaveLength(2)
    expect(screen.queryByText(long)).toBeNull()
    expect(screen.queryByText(summary)).toBeNull()
    await userEvent.click(more[0]!)
    expect(screen.getByText(long)).toBeInTheDocument()
    expect(screen.queryByText(summary)).toBeNull()
    expect(screen.getByRole('button', { name: 'Mostra meno' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mostra tutto' }))
    expect(screen.getByText(summary)).toBeInTheDocument()
  })

  it('filters by outcome through the pills, carried in the URL and sent to the API', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(200, {
        items: url.searchParams.get('esito') === 'errore' ? [FAILED] : [FILED, FAILED],
        next_cursor: null,
      })
    })
    const router = mount('/admin/team/proposte')
    await screen.findByText(FILED.descrizione)

    const pills = screen.getByRole('group', { name: 'Filtra per esito' })
    expect(within(pills).getAllByRole('button').map((pill) => pill.textContent)).toEqual(['Tutte', 'Riuscite', 'Fallite'])
    await userEvent.click(within(pills).getByRole('button', { name: 'Fallite' }))

    await waitFor(() => expect(screen.queryByText(FILED.descrizione)).toBeNull())
    expect(screen.getByText(FAILED.descrizione)).toBeInTheDocument()
    expect(spy.mock.calls.some((call) => String(call[0]) === '/api/hub/team/proposals?esito=errore')).toBe(true)
    expect(router.state.location.search).toEqual({ esito: 'errore' })
    expect(within(pills).getByRole('button', { name: 'Fallite' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(pills).getByRole('button', { name: 'Tutte' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('walks the cursor a page at a time with «Mostra altri»', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(
        200,
        url.searchParams.get('cursor') === 'CURSOR1'
          ? { items: [NEVER_FILED], next_cursor: null }
          : { items: [FILED], next_cursor: 'CURSOR1' },
      )
    })
    mount('/admin/team/proposte')

    await screen.findByText(FILED.descrizione)
    expect(screen.getByText('Mostrate 1 proposte, ce ne sono altre.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))

    await screen.findByText(NEVER_FILED.descrizione)
    expect(spy.mock.calls.some((call) => String(call[0]) === '/api/hub/team/proposals?cursor=CURSOR1')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })

  it('tells an empty list from a filter that narrowed it to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { items: [], next_cursor: null }))
    mount('/admin/team/proposte')
    expect(await screen.findByText('Ancora nessuna proposta chiesta.')).toBeInTheDocument()
  })

  it('names the filter when an outcome leaves nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { items: [], next_cursor: null }))
    mount('/admin/team/proposte?esito=errore')
    expect(await screen.findByText('Nessun risultato per questi filtri.')).toBeInTheDocument()
  })

  it('says so when the list cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(500, { detail: 'boom' }))
    mount('/admin/team/proposte')
    expect(await screen.findByText('Non riesco a leggere la lista.')).toBeInTheDocument()
  })
})
