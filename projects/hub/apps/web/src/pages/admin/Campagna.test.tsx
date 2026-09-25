import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCampagna } from './Campagna'

const DETAIL = {
  campagna: {
    id: 'c1', nome: 'Manca il CV', slug: 's', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null, oggetto: 'o', testo: 't',
    bottone_testo: 'b', bottone_meta: 'area', azione: 'cv', stato: 'programmata', contenuto_at: '2026-09-25T07:00:00Z',
    programmata_per: '2026-09-26T07:30:00Z', prova_inviata_at: '2026-09-25T07:05:00Z', inviata_at: null,
    created_at: '2026-09-25T07:00:00Z', pronta: true,
  },
  conteggi: { destinatari: 2, in_coda: 1, inviate: 0, saltate: 1, fallite: 0, consegnate: 0, rimbalzate: 0 },
  destinatari: [
    { id: 'r1', email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', stato: 'in_coda', motivo: null, inviata_at: null, consegnata_at: null, rimbalzata_at: null },
    { id: 'r2', email: 'bob@studio.it', nome: 'Bob', tipo: 'freelancer', stato: 'saltata', motivo: 'ha già fatto l’azione', inviata_at: null, consegnata_at: null, rimbalzata_at: null },
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
