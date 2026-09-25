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

/** A second template, its own `stato_percorso` and its own `azione` -- fix 4's own
 *  fixture, so switching from `TEMPLATE` to this one is a switch the saved `azione`
 *  has to show. */
const TEMPLATE2 = {
  stato_percorso: 'profilo_incompleto',
  etichetta: 'Profilo da completare',
  oggetto: 'Completa il profilo',
  testo: 'Ciao {nome},\n\ncompleta il profilo.',
  bottone_testo: 'Vai al profilo',
  bottone_meta: 'wizard',
  azione: 'scheda_completa',
}

const COUNTS_EMPTY = {
  destinatari: 0,
  in_coda: 0,
  inviate: 0,
  saltate: 0,
  fallite: 0,
  consegnate: 0,
  rimbalzate: 0,
}

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

/** The edit route (`/admin/campaigns/$id/edit`): same tree shape as `mount()`, with the
 *  edit path instead of `new` and the memory history starting there. */
function mountEdit(id: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const edit = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id/edit', component: AdminCreaCampagna })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: () => <p>pagina campagna</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([edit, one])]),
    history: createMemoryHistory({ initialEntries: [`/admin/campaigns/${id}/edit`] }),
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

describe('fix round 1 (REB-472)', () => {
  it('fix 1: a filtered campaign creates with a non-empty default name and the right filtri', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns': () =>
        json({ ...DRAFT, id: 'c2', nome: 'Campagna da filtri', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', stato: 'nuovo' } }, 201),
      'GET /api/hub/campaigns/c2/audience': () => json(AUDIENCE),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await userEvent.type(screen.getByLabelText('Stato'), 'nuovo')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByText('amministratore')
    const create = calls.mock.calls.find(([url, init]) => String(url).endsWith('/api/hub/campaigns') && init?.method === 'POST')!
    const body = JSON.parse(String(create[1]!.body))
    expect(body.nome).toBe('Campagna da filtri')
    expect(body.fonte).toBe('filtri')
    expect(body.filtri).toEqual({ lista: 'talenti', stato: 'nuovo' })
  })

  it('fix 2: the edit route reads the list from filtri.lista, not from which fields are present', async () => {
    const editCampaign = { ...DRAFT, id: 'c1', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', stato: 'nuovo' } }
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      // `api()` matches the first registered prefix a request starts with, so the
      // longer `/audience` path has to be registered before the plain campaign path it
      // would otherwise shadow (`c1/audience`.startsWith(`c1`) is true too).
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c1': () => json({ campagna: editCampaign, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'PATCH /api/hub/campaigns/c1': () => json({ ...editCampaign, pronta: false }),
    })
    mountEdit('c1')
    // Talenti-only field: only present once the seeded `lista` really reads 'talenti'
    // from `filtri.lista`, not the old (buggy) has_cv/con_accessi presence guess.
    expect(await screen.findByLabelText('Ha un CV')).toBeInTheDocument()
    expect(screen.getByLabelText('Stato')).toHaveValue('nuovo')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByText('amministratore')
    const patch = calls.mock.calls.find(([url, init]) => String(url).endsWith('/campaigns/c1') && init?.method === 'PATCH')!
    const body = JSON.parse(String(patch[1]!.body))
    expect(body.filtri.lista).toBe('talenti')
  })

  it('fix 3: changing a filter after loading the audience preview reloads it', async () => {
    let audienceCalls = 0
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, id: 'c3', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c3': () =>
        json({ ...DRAFT, id: 'c3', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', q: 'ada' } }),
      'GET /api/hub/campaigns/c3/audience': () => ((audienceCalls += 1), json(AUDIENCE)),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByText('amministratore')
    expect(audienceCalls).toBe(1)
    await userEvent.type(screen.getByLabelText('Cerca'), 'ada')
    expect(screen.queryByText('amministratore')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByText('amministratore')
    expect(audienceCalls).toBe(2)
  })

  it('fix 4: the edit route always updates the disabled action select on a new state pick', async () => {
    const editCampaign = { ...DRAFT, id: 'c4', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null }
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE, TEMPLATE2]),
      // Same ordering note as fix 2's test: the `/audience` prefix has to be registered
      // before the plain campaign path it would otherwise shadow.
      'GET /api/hub/campaigns/c4/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c4': () => json({ campagna: editCampaign, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'PATCH /api/hub/campaigns/c4': () =>
        json({ ...editCampaign, stato_percorso: 'profilo_incompleto', azione: 'scheda_completa', pronta: false }),
    })
    mountEdit('c4')
    await userEvent.click(await screen.findByRole('combobox', { name: 'Stato del percorso' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Profilo da completare' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByText('amministratore')
    const patch = calls.mock.calls.find(([url, init]) => String(url).endsWith('/campaigns/c4') && init?.method === 'PATCH')!
    const body = JSON.parse(String(patch[1]!.body))
    expect(body.azione).toBe('scheda_completa')
  })

  it('fix 5: the audience preview uses the shared Table primitive, not a raw <table>', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
    })
    mount()
    await userEvent.click(await screen.findByRole('combobox', { name: 'Stato del percorso' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Manca solo il CV' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    const table = await screen.findByRole('table')
    expect(table.closest('[data-slot="table-container"]')).not.toBeNull()
  })
})
