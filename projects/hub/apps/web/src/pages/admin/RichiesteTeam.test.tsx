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
import type { TeamRequestsFilters } from '@/lib/api'
import { strParam } from '@/router'
import { AdminRichiesteTeam } from './RichiesteTeam'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const ACME = {
  id: 'r1',
  azienda: 'Acme S.r.l.',
  origine: 'pubblico',
  stato: 'nuova',
  created_at: '2026-09-25T10:00:00Z',
  contacted_at: null,
  talenti_totale: 3,
  talenti_si: 1,
}

const BIANCHI = {
  ...ACME,
  id: 'r2',
  azienda: 'Bianchi Srl',
  origine: 'cloud',
  stato: 'contattata',
  created_at: '2026-09-20T10:00:00Z',
  contacted_at: '2026-09-21T10:00:00Z',
  talenti_totale: 2,
  talenti_si: 0,
}

/** Mirrors `Matches.test.tsx`'s own `mount`: the pathless `signedIn` id, `/admin/team`
 *  with the `validateSearch` `router.tsx` gives it, and a stub for a request's page. */
function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const list = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/team',
    component: AdminRichiesteTeam,
    validateSearch: (search: Record<string, unknown>): TeamRequestsFilters => ({
      stato: strParam(search.stato),
    }),
  })
  const page = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/team/$id',
    component: () => <p>richiesta</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([list, page])]),
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

describe('«Richieste team» (REB-514, spec § 3.5)', () => {
  it('lists each request with its company, origin, date, state and how many talents said yes', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { items: [ACME, BIANCHI], next_cursor: null }),
    )
    mount('/admin/team')

    expect(await screen.findByRole('heading', { level: 1, name: 'Richieste team' })).toBeInTheDocument()
    const acme = (await screen.findByRole('link', { name: 'Acme S.r.l.' })).closest('tr')!
    expect(within(acme).getByRole('link', { name: 'Acme S.r.l.' }).getAttribute('href')).toMatch(/\/admin\/team\/r1$/)
    expect(cellUnder(acme, 'Origine')).toHaveTextContent('Pubblico')
    expect(cellUnder(acme, 'Arrivata')).toHaveTextContent('25 set 2026')
    expect(cellUnder(acme, 'Stato')).toHaveTextContent('Nuova')
    expect(cellUnder(acme, 'Talenti')).toHaveTextContent('1 sì su 3')

    const bianchi = screen.getByRole('link', { name: 'Bianchi Srl' }).closest('tr')!
    expect(cellUnder(bianchi, 'Origine')).toHaveTextContent('Cloud')
    expect(cellUnder(bianchi, 'Stato')).toHaveTextContent('Contattata')
    expect(cellUnder(bianchi, 'Talenti')).toHaveTextContent('0 sì su 2')

    expect(spy).toHaveBeenCalledWith('/api/hub/team/requests', expect.anything())
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })

  it('filters by state through the pills, carried in the URL and sent to the API', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(200, {
        items: url.searchParams.get('stato') === 'contattata' ? [BIANCHI] : [ACME, BIANCHI],
        next_cursor: null,
      })
    })
    const router = mount('/admin/team')
    await screen.findByRole('link', { name: 'Acme S.r.l.' })

    const pills = screen.getByRole('group', { name: 'Filtra per stato' })
    expect(within(pills).getAllByRole('button').map((pill) => pill.textContent)).toEqual([
      'Tutti',
      'Nuova',
      'Contattata',
      'Chiusa',
    ])
    await userEvent.click(within(pills).getByRole('button', { name: 'Contattata' }))

    await waitFor(() => expect(screen.queryByRole('link', { name: 'Acme S.r.l.' })).toBeNull())
    expect(screen.getByRole('link', { name: 'Bianchi Srl' })).toBeInTheDocument()
    expect(spy.mock.calls.some((call) => String(call[0]) === '/api/hub/team/requests?stato=contattata')).toBe(true)
    expect(router.state.location.search).toEqual({ stato: 'contattata' })
  })

  it('reads the state back out of a URL a link already carries', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { items: [BIANCHI], next_cursor: null }))
    mount('/admin/team?stato=chiusa')
    await screen.findByRole('link', { name: 'Bianchi Srl' })
    expect(spy).toHaveBeenCalledWith('/api/hub/team/requests?stato=chiusa', expect.anything())
    const pills = screen.getByRole('group', { name: 'Filtra per stato' })
    expect(within(pills).getByRole('button', { name: 'Chiusa' })).toHaveAttribute('data-variant', 'default')
  })

  it('walks the cursor a page at a time with «Mostra altri»', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(
        200,
        url.searchParams.get('cursor') === 'CURSOR1'
          ? { items: [BIANCHI], next_cursor: null }
          : { items: [ACME], next_cursor: 'CURSOR1' },
      )
    })
    mount('/admin/team')

    await screen.findByRole('link', { name: 'Acme S.r.l.' })
    expect(screen.queryByRole('link', { name: 'Bianchi Srl' })).toBeNull()
    expect(screen.getByText('Mostrate 1 richieste, ce ne sono altre.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))

    await screen.findByRole('link', { name: 'Bianchi Srl' })
    expect(screen.getByRole('link', { name: 'Acme S.r.l.' })).toBeInTheDocument()
    expect(spy.mock.calls.some((call) => String(call[0]) === '/api/hub/team/requests?cursor=CURSOR1')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })

  it('tells an empty list from a filter that narrowed it to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { items: [], next_cursor: null }))
    mount('/admin/team')
    expect(await screen.findByText('Ancora nessuna richiesta di team.')).toBeInTheDocument()
  })

  it('names the filter when a state leaves nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { items: [], next_cursor: null }))
    mount('/admin/team?stato=chiusa')
    expect(await screen.findByText('Nessun risultato per questi filtri.')).toBeInTheDocument()
  })

  it('says so when the list cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(500, { detail: 'boom' }))
    mount('/admin/team')
    expect(await screen.findByText('Non riesco a leggere la lista.')).toBeInTheDocument()
  })
})
