import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCampagne } from './Campagne'

const ITEM = {
  id: 'c1',
  nome: 'Manca il CV',
  slug: 'c-2026-09-25-manca-il-cv',
  fonte: 'stato',
  stato_percorso: 'manca_cv',
  filtri: null,
  oggetto: 'Manca solo il CV',
  testo: 'Ciao {nome},',
  bottone_testo: 'Carica il CV',
  bottone_meta: 'area',
  azione: 'cv',
  stato: 'inviata',
  contenuto_at: '2026-09-25T07:00:00Z',
  programmata_per: '2026-09-25T07:30:00Z',
  prova_inviata_at: '2026-09-25T07:10:00Z',
  inviata_at: '2026-09-25T07:32:00Z',
  created_at: '2026-09-25T07:00:00Z',
  pronta: true,
  conteggi: { destinatari: 9, in_coda: 0, inviate: 8, saltate: 1, fallite: 0, consegnate: 8, rimbalzate: 0 },
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const list = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns', component: AdminCampagne })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: () => <p>campagna</p> })
  const fresh = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/new', component: () => <p>nuova</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([list, one, fresh])]),
    history: createMemoryHistory({ initialEntries: ['/admin/campaigns'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Campagne»', () => {
  it('lists each campaign with its state and numbers, and links to it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [ITEM] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    const row = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(row).getByText('Inviata')).toBeInTheDocument()
    expect(row).toHaveTextContent('8 inviate')
    expect(row).toHaveTextContent('1 saltate')
    expect(screen.getByRole('link', { name: 'Nuova campagna' })).toHaveAttribute('href', expect.stringMatching(/\/admin\/campaigns\/new$/))
  })

  it('says so when there is none', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    expect(await screen.findByText('Ancora nessuna campagna.')).toBeInTheDocument()
  })
})
