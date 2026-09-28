import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
  fermo_at: null,
  fermo_motivo: null,
  pronta: true,
  conteggi: { destinatari: 11, in_coda: 0, inviate: 8, saltate: 1, fallite: 2, consegnate: 8, rimbalzate: 0, cliccate: 4, entrate: 3, azioni: 2 },
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

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('«Campagne»', () => {
  it('lists each campaign with its state and numbers, and links to it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [ITEM] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    const row = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(row).getByText('Inviata')).toBeInTheDocument()
    expect(row).toHaveTextContent('8 inviate · 8 consegnate · 4 clic · 3 entrati · 2 CV caricati · 1 saltate · 2 fallite')
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

describe('«Campagne» since REB-524', () => {
  it('reads «Invio fermo» and the reason on a stopped send', async () => {
    const stopped = { ...ITEM, stato: 'in_invio', inviata_at: null, fermo_at: '2026-09-25T07:31:00Z', fermo_motivo: 'Resend rifiuta la chiave' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [stopped] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    const row = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(row).getByText('Invio fermo')).toBeInTheDocument()
    expect(within(row).getByText('Resend rifiuta la chiave')).toBeInTheDocument()
    expect(within(row).queryByText('In invio')).not.toBeInTheDocument()
  })

  it('deletes a draft from its row after a second click, and offers it on no other row', async () => {
    const draft = { ...ITEM, id: 'c2', nome: 'Bozza vecchia', stato: 'bozza', programmata_per: null, inviata_at: null }
    let items: unknown[] = [ITEM, draft]
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'DELETE') {
        items = [ITEM]
        return new Response(null, { status: 204 })
      }
      return new Response(JSON.stringify({ items }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    mount()
    const sent = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(sent).queryByRole('button', { name: 'Elimina' })).not.toBeInTheDocument()
    const row = screen.getByRole('link', { name: 'Bozza vecchia' }).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: 'Elimina' }))
    expect(within(row).getByText('Eliminare la bozza?')).toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: 'Conferma' }))
    await vi.waitFor(() => expect(screen.queryByRole('link', { name: 'Bozza vecchia' })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Manca il CV' })).toBeInTheDocument()
    const call = fetch.mock.calls.find(([, init]) => init?.method === 'DELETE')!
    expect(String(call[0])).toMatch(/\/api\/hub\/campaigns\/c2$/)
  })
})

describe('«Campagne» rereads itself while a send is under way (REB-524)', () => {
  const list = (items: unknown[]) =>
    new Response(JSON.stringify({ items }), { status: 200, headers: { 'Content-Type': 'application/json' } })

  it('shows a send that stops while the admin is on the page, without a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const sending = { ...ITEM, stato: 'in_invio', inviata_at: null }
    const stopped = { ...sending, fermo_at: '2026-09-25T07:31:00Z', fermo_motivo: 'Resend rifiuta la chiave' }
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(list([sending]))
      .mockResolvedValue(list([stopped]))
    mount()
    const row = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(row).getByText('In invio')).toBeInTheDocument()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(await screen.findByText('Invio fermo')).toBeInTheDocument()
    expect(screen.getByText('Resend rifiuta la chiave')).toBeInTheDocument()
    expect(fetch.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('stays still when nothing is scheduled or sending', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => list([ITEM]))
    mount()
    await screen.findByRole('link', { name: 'Manca il CV' })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
