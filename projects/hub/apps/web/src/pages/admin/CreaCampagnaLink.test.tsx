import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCreaCampagna } from './CreaCampagna'

/** «Un link» in the one-page editor (REB-530): the address field, what it refuses as
 *  it is typed, the click as the action, and the preview that leads there. */

const LUMA = 'https://lu.ma/rebase-house'
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
  id: 'c1', nome: 'Manca solo il CV', slug: 's', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null, segue_id: null,
  oggetto: TEMPLATE.oggetto, testo: TEMPLATE.testo, bottone_testo: TEMPLATE.bottone_testo, bottone_meta: 'area', bottone_url: null,
  azione: 'cv', stato: 'bozza', contenuto_at: '2026-09-25T07:00:00Z', programmata_per: null, prova_inviata_at: null,
  inviata_at: null, created_at: '2026-09-25T07:00:00Z', pronta: false,
}
const AUDIENCE = { righe: [{ email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', escluso: null }], incluse: 1, escluse: 0 }
const ME = { email: 'ivan@rebase.it', nome: 'Ivan', role: 'admin' }
const COUNTS_EMPTY = { destinatari: 0, in_coda: 0, inviate: 0, saltate: 0, fallite: 0, consegnate: 0, rimbalzate: 0 }
const SAVED = { timeout: 3000 }

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Answers by method and path, the first registered prefix winning, and records every
 *  call. A PATCH answers the draft with the body merged in, as the server stores it. */
function api(routes: Record<string, (init?: RequestInit) => Response | Promise<Response>>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const match = Object.keys(routes).find((prefix) => key.startsWith(prefix))
    return match ? routes[match]!(init) : json({ detail: `unexpected ${key}` }, 500)
  })
}

function mountAt(path: string, entry: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => signedIn, path, component: AdminCreaCampagna })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: () => <p>pagina campagna</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([page, one])]),
    history: createMemoryHistory({ initialEntries: [entry] }),
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

async function pick(label: string, option: string) {
  await userEvent.click(await screen.findByRole('combobox', { name: label }))
  await userEvent.click(await screen.findByRole('option', { name: option }))
}

async function options(label: string): Promise<string[]> {
  await userEvent.click(await screen.findByRole('combobox', { name: label }))
  const names = (await screen.findAllByRole('option')).map((option) => option.textContent ?? '')
  await userEvent.keyboard('{Escape}')
  return names
}

type Calls = ReturnType<typeof api>

function patches(calls: Calls, id: string) {
  return calls.mock.calls
    .filter(([url, init]) => init?.method === 'PATCH' && String(url).endsWith(`/api/hub/campaigns/${id}`))
    .map(([, init]) => JSON.parse(String(init!.body)))
}

function preview() {
  return within(screen.getByTestId('anteprima-mail'))
}

describe('«Un link» (REB-530)', () => {
  it('asks for the address, refuses a wrong one as it is typed, and saves the link with the click', async () => {
    let stored: Record<string, unknown> = DRAFT
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns/c1/test': () => json({ ...stored, pronta: true, prova_inviata_at: '2026-09-25T07:05:00Z' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': (init) => {
        const body = JSON.parse(String(init!.body)) as Record<string, unknown>
        stored = { ...stored, bottone_url: null, ...body }
        return json(stored)
      },
    })
    mountAt('/admin/campaigns/new', '/admin/campaigns/new')
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    expect(screen.queryByLabelText('Indirizzo del link')).not.toBeInTheDocument()

    await pick('Dove porta', 'Un link')
    const field = screen.getByLabelText('Indirizzo del link')
    expect(field).toHaveValue('')
    expect(screen.getByText('Ha cliccato il link')).toBeInTheDocument()
    expect(screen.getByText('Di una pagina fuori dal hub vediamo solo il clic sul bottone.')).toBeInTheDocument()
    // Not saved, and not sent to be refused: the header and the send bar say why.
    expect(screen.getByText('Non salvata: Scrivi il link a cui porta il bottone.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mandami una prova' })).toBeDisabled()

    await userEvent.type(field, 'http://lu.ma/rebase-house')
    expect(field).toHaveAccessibleDescription('Il link del bottone deve iniziare con https://.')
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('region', { name: 'Invio' })).toHaveTextContent('Il link del bottone deve iniziare con https://.')
    await new Promise((resolve) => setTimeout(resolve, 800))
    expect(patches(calls, 'c1')).toEqual([])

    await userEvent.clear(field)
    await userEvent.type(field, LUMA)
    await vi.waitFor(() => expect(patches(calls, 'c1')).toHaveLength(1), SAVED)
    expect(patches(calls, 'c1')[0]).toMatchObject({ bottone_meta: 'link', bottone_url: LUMA, azione: 'clic' })
    expect(await screen.findByText(/Bozza salvata alle/, {}, SAVED)).toBeInTheDocument()
    // The preview prints the address under the button, as the mail does, and opens it.
    expect(preview().getByText('Se il bottone non si apre, copia questo indirizzo nel browser:')).toBeInTheDocument()
    expect(preview().getByRole('link', { name: LUMA })).toHaveAttribute('href', LUMA)

    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    expect(await screen.findByText(/Prova inviata alle .* a ivan@rebase\.it\. Puoi inviare\./)).toBeInTheDocument()

    // Back to the hub: the state's own action, and no address in the request.
    await pick('Dove porta', 'La sua area')
    expect(screen.queryByLabelText('Indirizzo del link')).not.toBeInTheDocument()
    expect(screen.getByText('Ha caricato il CV')).toBeInTheDocument()
    await vi.waitFor(() => expect(patches(calls, 'c1')).toHaveLength(2), SAVED)
    expect(patches(calls, 'c1')[1]).toMatchObject({ bottone_meta: 'area', azione: 'cv' })
    expect(patches(calls, 'c1')[1]).not.toHaveProperty('bottone_url')
    expect(preview().queryByRole('link')).not.toBeInTheDocument()
  })

  it('takes the menu of actions away from a filtered list while it leads to a link', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c1': () => json(DRAFT),
    })
    mountAt('/admin/campaigns/new', '/admin/campaigns/new')
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await pick('Cosa misuriamo', 'Ha caricato il CV')
    await pick('Dove porta', 'Un link')
    expect(screen.queryByRole('combobox', { name: 'Cosa misuriamo' })).not.toBeInTheDocument()
    expect(screen.getByText('Ha cliccato il link')).toBeInTheDocument()
    await pick('Dove porta', 'Il wizard del profilo')
    expect(screen.getByRole('combobox', { name: 'Cosa misuriamo' })).toHaveTextContent('È entrato nell’area')
    expect(await options('Cosa misuriamo')).not.toContain('Ha cliccato il link')
  })

  it('keeps a «Riscrivi» of a link on a link, and saves a new address as its mail', async () => {
    const parent = { ...DRAFT, id: 'c0', nome: 'Casa', stato: 'inviata', bottone_meta: 'link', bottone_url: LUMA, azione: 'clic' }
    const lista = { ...parent, id: 'c9', nome: 'Casa · riscrivi', stato: 'bozza', fonte: 'lista', stato_percorso: null, segue_id: 'c0' }
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c9/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c9': () => json({ campagna: lista, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'GET /api/hub/campaigns/c0': () => json({ campagna: parent, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'PATCH /api/hub/campaigns/c9': () => json({ ...lista, bottone_url: `${LUMA}-2` }),
    })
    mountAt('/admin/campaigns/$id/edit', '/admin/campaigns/c9/edit')
    expect(await screen.findByLabelText('Indirizzo del link')).toHaveValue(LUMA)
    expect(await options('Dove porta')).toEqual(['Un link'])
    await userEvent.type(screen.getByLabelText('Indirizzo del link'), '-2')
    await vi.waitFor(() => expect(patches(calls, 'c9')).toHaveLength(1), SAVED)
    expect(patches(calls, 'c9')[0]).toEqual({
      nome: 'Casa · riscrivi',
      oggetto: DRAFT.oggetto,
      testo: DRAFT.testo,
      bottone_testo: DRAFT.bottone_testo,
      bottone_meta: 'link',
      bottone_url: `${LUMA}-2`,
    })
  })
})
