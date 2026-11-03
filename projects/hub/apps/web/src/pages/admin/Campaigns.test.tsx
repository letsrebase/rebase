import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCampaigns } from './Campaigns'

const ITEM = {
  id: 'c1',
  nome: 'Manca il CV',
  slug: 'c-2026-09-25-manca-il-cv',
  source: 'status',
  journey_status: 'missing_cv',
  filters: null,
  subject: 'Manca solo il CV',
  text: 'Ciao {nome},',
  button_text: 'Carica il CV',
  button_meta: 'area',
  action: 'cv',
  status: 'sent',
  content_at: '2026-09-25T07:00:00Z',
  scheduled_for: '2026-09-25T07:30:00Z',
  test_sent_at: '2026-09-25T07:10:00Z',
  sent_at: '2026-09-25T07:32:00Z',
  created_at: '2026-09-25T07:00:00Z',
  stalled_at: null,
  stalled_reason: null,
  ready: true,
  counts: { recipients: 11, in_coda: 0, sent: 8, skipped: 1, failed: 2, delivered: 8, bounced: 0, clicked: 4, income: 3, actions: 2 },
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const list = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns', component: AdminCampaigns })
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
    const stopped = { ...ITEM, status: 'sending', sent_at: null, stalled_at: '2026-09-25T07:31:00Z', stalled_reason: 'Resend rifiuta l’invio: controlla la chiave e il dominio del mittente' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [stopped] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    const row = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(row).getByText('Invio fermo')).toBeInTheDocument()
    expect(within(row).getByText('Resend rifiuta l’invio: controlla la chiave e il dominio del mittente')).toBeInTheDocument()
    expect(within(row).queryByText('In invio')).not.toBeInTheDocument()
  })

  it('deletes a draft from its row after a second click, and offers it on no other row', async () => {
    const draft = { ...ITEM, id: 'c2', nome: 'Bozza vecchia', status: 'draft', scheduled_for: null, sent_at: null }
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
    const sending = { ...ITEM, status: 'sending', sent_at: null }
    const stopped = { ...sending, stalled_at: '2026-09-25T07:31:00Z', stalled_reason: 'Resend rifiuta l’invio: controlla la chiave e il dominio del mittente' }
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(list([sending]))
      .mockResolvedValue(list([stopped]))
    mount()
    const row = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(row).getByText('In invio')).toBeInTheDocument()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(await screen.findByText('Invio fermo')).toBeInTheDocument()
    expect(screen.getByText('Resend rifiuta l’invio: controlla la chiave e il dominio del mittente')).toBeInTheDocument()
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
