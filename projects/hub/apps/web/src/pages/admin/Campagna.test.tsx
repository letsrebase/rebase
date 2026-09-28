import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCampagna } from './Campagna'

const DETAIL = {
  campagna: {
    id: 'c1', nome: 'Manca il CV', slug: 's', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null, segue_id: null, oggetto: 'o', testo: 't',
    bottone_testo: 'b', bottone_meta: 'area', azione: 'cv', stato: 'programmata', contenuto_at: '2026-09-25T07:00:00Z',
    programmata_per: '2026-09-26T07:30:00Z', prova_inviata_at: '2026-09-25T07:05:00Z', inviata_at: null,
    created_at: '2026-09-25T07:00:00Z', pronta: true,
  },
  conteggi: { destinatari: 2, in_coda: 1, inviate: 0, saltate: 1, fallite: 0, consegnate: 0, rimbalzate: 0, cliccate: 0, entrate: 0, azioni: 0 },
  destinatari: [
    { id: 'r1', email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', stato: 'in_coda', motivo: null, inviata_at: null, consegnata_at: null, rimbalzata_at: null, primo_clic_at: null, reclamo_at: null, entrato_at: null, azione_at: null, entrato_dalla_mail: false, azione_dalla_mail: false },
    { id: 'r2', email: 'bob@studio.it', nome: 'Bob', tipo: 'freelancer', stato: 'saltata', motivo: 'ha già fatto l’azione', inviata_at: null, consegnata_at: null, rimbalzata_at: null, primo_clic_at: null, reclamo_at: null, entrato_at: null, azione_at: null, entrato_dalla_mail: false, azione_dalla_mail: false },
  ],
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: AdminCampagna })
  const edit = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id/edit', component: () => <p>modifica</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([one, edit])]),
    history: createMemoryHistory({ initialEntries: ['/admin/campaigns/c1'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the campaign page', () => {
  it('shows the numbers, each person with the reason a row was skipped, and the scheduled actions', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    const bob = (await screen.findByText('bob@studio.it')).closest('tr')!
    expect(within(bob).getByText('Saltata')).toBeInTheDocument()
    expect(within(bob).getByText('ha già fatto l’azione')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riporta in bozza' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annulla' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Modifica' })).not.toBeInTheDocument()
  })

  it('adds a person to «Non scrivere mai» from their row, after a second click like «Annulla»', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).endsWith('/never-write') ? json({ ok: true }) : json(DETAIL),
    )
    mount()
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    await userEvent.click(within(ada).getByRole('button', { name: 'Non scrivere mai' }))
    expect(within(ada).getByText('Non scrivere più a questa persona?')).toBeInTheDocument()
    await userEvent.click(within(ada).getByRole('button', { name: 'Indietro' }))
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/never-write'))).toBe(false)
    await userEvent.click(within(ada).getByRole('button', { name: 'Non scrivere mai' }))
    await userEvent.click(within(ada).getByRole('button', { name: 'Conferma' }))
    expect(await within(ada).findByText('Non riceverà più campagne')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/never-write'))!
    expect(JSON.parse(String(call[1]!.body))).toEqual({ email: 'ada@studio.it' })
  })

  it('says when a scheduled campaign leaves, in Rome time', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    expect(await screen.findByText('Parte il 26 settembre 2026 alle 09:30 (ora di Roma)')).toBeInTheDocument()
  })

  it('says when a sent campaign left, in Rome time', async () => {
    const sent = { ...DETAIL, campagna: { ...DETAIL.campagna, stato: 'inviata', inviata_at: '2026-09-26T07:31:00Z' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(sent))
    mount()
    expect(await screen.findByText('Inviata il 26 settembre 2026 alle 09:31')).toBeInTheDocument()
    expect(screen.queryByText(/^Parte il/)).not.toBeInTheDocument()
  })
})

const SENT = {
  campagna: { ...DETAIL.campagna, stato: 'inviata', inviata_at: '2026-09-26T07:31:00Z', azione: 'cv' },
  conteggi: { destinatari: 3, in_coda: 0, inviate: 3, saltate: 0, fallite: 0, consegnate: 3, rimbalzate: 0, cliccate: 2, entrate: 2, azioni: 1 },
  destinatari: [
    { id: 'r1', email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', stato: 'inviata', motivo: null, inviata_at: '2026-09-26T07:31:00Z', consegnata_at: '2026-09-26T07:32:00Z', rimbalzata_at: null, primo_clic_at: '2026-09-26T08:00:00Z', reclamo_at: null, entrato_at: '2026-09-26T08:01:00Z', azione_at: '2026-09-26T08:10:00Z', entrato_dalla_mail: true, azione_dalla_mail: false },
    { id: 'r2', email: 'bob@studio.it', nome: 'Bob', tipo: 'freelancer', stato: 'inviata', motivo: null, inviata_at: '2026-09-26T07:31:00Z', consegnata_at: '2026-09-26T07:32:00Z', rimbalzata_at: null, primo_clic_at: '2026-09-26T09:00:00Z', reclamo_at: null, entrato_at: '2026-09-26T09:01:00Z', azione_at: null, entrato_dalla_mail: false, azione_dalla_mail: false },
    { id: 'r3', email: 'cleo@studio.it', nome: 'Cleo', tipo: 'freelancer', stato: 'inviata', motivo: null, inviata_at: '2026-09-26T07:31:00Z', consegnata_at: '2026-09-26T07:32:00Z', rimbalzata_at: null, primo_clic_at: null, reclamo_at: null, entrato_at: null, azione_at: null, entrato_dalla_mail: false, azione_dalla_mail: false },
  ],
}

describe('the outcome of a sent campaign (phase 2)', () => {
  it('shows each figure with its share of the mails sent', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    const clicks = (await screen.findByText('Cliccate')).closest('div')!
    expect(clicks).toHaveTextContent('2')
    expect(clicks).toHaveTextContent('67%')
    const done = screen.getByText('Hanno caricato il CV').closest('div')!
    expect(done).toHaveTextContent('1')
    expect(done).toHaveTextContent('33%')
  })

  it('shows Saltate as a share of the list, not of the mails sent, since a skipped row never left', async () => {
    const withSkips = { ...SENT, conteggi: { ...SENT.conteggi, destinatari: 4, saltate: 1 } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(withSkips))
    mount()
    const saltate = (await screen.findByText('Saltate')).closest('div')!
    expect(saltate).toHaveTextContent('1')
    expect(saltate).toHaveTextContent('25%')
  })

  it('says «dalla mail» where the mail was the door', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(ada).getAllByText('dalla mail')).toHaveLength(1)
  })

  it('filters who did the action and who did nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    await screen.findByText('ada@studio.it')
    await userEvent.click(screen.getByRole('button', { name: 'Non ha fatto niente (2)' }))
    expect(screen.queryByText('ada@studio.it')).not.toBeInTheDocument()
    expect(screen.getByText('bob@studio.it')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ha fatto l’azione (1)' }))
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(screen.queryByText('cleo@studio.it')).not.toBeInTheDocument()
  })

  it('«Riscrivi a chi non ha fatto niente» opens the new draft', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) =>
      String(input).endsWith('/follow-up') && init?.method === 'POST'
        ? json({ ...SENT.campagna, id: 'c2', fonte: 'lista', segue_id: 'c1', stato: 'bozza' }, 201)
        : json(SENT),
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Riscrivi a chi non ha fatto niente (2)' }))
    expect(await screen.findByText('modifica')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/api/hub/campaigns/c1/follow-up'))).toBe(true)
  })

  it('offers no «Riscrivi» before the send or once everyone acted', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    await screen.findByText('ada@studio.it')
    expect(screen.queryByRole('button', { name: /Riscrivi/ })).not.toBeInTheDocument()
  })
})
