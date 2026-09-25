import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCreaCampagna } from './CreaCampagna'

const TEMPLATE = {
  stato_percorso: 'manca_cv',
  etichetta: 'Manca solo il CV',
  oggetto: 'Manca solo il CV',
  testo: 'Ciao {nome},\n\nmanca il CV.',
  bottone_testo: 'Carica il CV',
  bottone_meta: 'area',
  azione: 'cv',
}
const DRAFT = {
  id: 'c1', nome: 'Manca solo il CV', slug: 's', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null,
  oggetto: TEMPLATE.oggetto, testo: TEMPLATE.testo, bottone_testo: TEMPLATE.bottone_testo, bottone_meta: 'area', azione: 'cv',
  stato: 'bozza', contenuto_at: '2026-09-25T07:00:00Z', programmata_per: null, prova_inviata_at: null, inviata_at: null,
  created_at: '2026-09-25T07:00:00Z', pronta: false,
}
const AUDIENCE = {
  righe: [
    { email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', escluso: null },
    { email: 'ivan@rebase.it', nome: 'Ivan', tipo: 'freelancer', escluso: 'amministratore' },
  ],
  incluse: 1,
  escluse: 1,
}
const ME = { email: 'ivan@rebase.it', nome: 'Ivan', role: 'admin' }

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Answers by method and path, and records every call for the assertions. */
function api(routes: Record<string, () => Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const match = Object.keys(routes).find((prefix) => key.startsWith(prefix))
    return match ? routes[match]!() : json({ detail: `unexpected ${key}` }, 500)
  })
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const fresh = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/new', component: AdminCreaCampagna })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: () => <p>pagina campagna</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([fresh, one])]),
    history: createMemoryHistory({ initialEntries: ['/admin/campaigns/new'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Nuova campagna»', () => {
  it('fills the mail from the state, shows who is left out and why, and sends after a test', async () => {
    let tested = false
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns/c1/test': () => ((tested = true), json({ ...DRAFT, pronta: true, prova_inviata_at: '2026-09-25T07:05:00Z' })),
      'POST /api/hub/campaigns/c1/schedule': () => json({ ...DRAFT, stato: 'programmata' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, pronta: tested }),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
    })
    mount()
    await userEvent.click(await screen.findByRole('combobox', { name: 'Stato del percorso' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Manca solo il CV' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByText('amministratore')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'ivan@rebase.it' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // to Cosa
    expect(screen.getByLabelText('Oggetto')).toHaveValue('Manca solo il CV')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // to Prova
    expect(await screen.findByText(/Ciao Ada,/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    expect(await screen.findByText('Prova inviata a ivan@rebase.it')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // to Quando
    await userEvent.click(screen.getByRole('button', { name: 'Invia' }))
    expect(await screen.findByText('pagina campagna')).toBeInTheDocument()
    const schedule = calls.mock.calls.find(([url, init]) => String(url).endsWith('/schedule') && init?.method === 'POST')!
    expect(JSON.parse(String(schedule[1]!.body))).toEqual({ esclusi: [] })
  })

  it('keeps «Invia» off after an edit that follows the test', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns/c1/test': () => json({ ...DRAFT, pronta: true, prova_inviata_at: '2026-09-25T07:05:00Z' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, pronta: false }),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
    })
    mount()
    await userEvent.click(await screen.findByRole('combobox', { name: 'Stato del percorso' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Manca solo il CV' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByText('amministratore')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Cosa
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Prova
    await userEvent.click(await screen.findByRole('button', { name: 'Mandami una prova' }))
    await screen.findByText('Prova inviata a ivan@rebase.it')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // back to Cosa
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // saves: pronta false
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Quando
    expect(screen.getByRole('button', { name: 'Invia' })).toBeDisabled()
    expect(screen.getByText(/Hai modificato la campagna dopo la prova/)).toBeInTheDocument()
  })
})
