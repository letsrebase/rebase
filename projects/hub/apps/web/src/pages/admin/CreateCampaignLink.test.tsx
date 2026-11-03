import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCreateCampaign } from './CreateCampaign'

/** «Un link» in the one-page editor (REB-530): the address field, what it refuses as
 *  it is typed, the click as the action, and the preview that leads there. */

const LUMA = 'https://lu.ma/rebase-house'
const TEMPLATE = {
  journey_status: 'missing_cv',
  label: 'Manca solo il CV',
  subject: 'Manca solo il CV',
  text: 'Ciao {nome},\n\nmanca il CV.',
  button_text: 'Carica il CV',
  button_meta: 'area',
  action: 'cv',
}
const DRAFT = {
  id: 'c1', nome: 'Manca solo il CV', slug: 's', source: 'status', journey_status: 'missing_cv', filters: null, segue_id: null,
  subject: TEMPLATE.subject, text: TEMPLATE.text, button_text: TEMPLATE.button_text, button_meta: 'area', button_url: null,
  action: 'cv', status: 'draft', content_at: '2026-09-25T07:00:00Z', scheduled_for: null, test_sent_at: null,
  sent_at: null, created_at: '2026-09-25T07:00:00Z', ready: false,
}
const AUDIENCE = { lines: [{ email: 'ada@studio.it', nome: 'Ada', kind: 'freelancer', excluded: null }], included: 1, excluded: 0 }
const ME = { email: 'ivan@rebase.it', nome: 'Ivan', role: 'admin' }
const COUNTS_EMPTY = { recipients: 0, in_coda: 0, sent: 0, skipped: 0, failed: 0, delivered: 0, bounced: 0 }
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
  const page = createRoute({ getParentRoute: () => signedIn, path, component: AdminCreateCampaign })
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
      'POST /api/hub/campaigns/c1/test': () => json({ ...stored, ready: true, test_sent_at: '2026-09-25T07:05:00Z' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': (init) => {
        const body = JSON.parse(String(init!.body)) as Record<string, unknown>
        stored = { ...stored, button_url: null, ...body }
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
    expect(patches(calls, 'c1')[0]).toMatchObject({ button_meta: 'link', button_url: LUMA, action: 'click' })
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
    expect(patches(calls, 'c1')[1]).toMatchObject({ button_meta: 'area', action: 'cv' })
    expect(patches(calls, 'c1')[1]).not.toHaveProperty('button_url')
    expect(preview().queryByRole('link')).not.toBeInTheDocument()
  })

  it('fills the mail from a state picked after «Un link», and keeps the link (CodeRabbit on #476)', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, button_meta: 'link', button_url: LUMA, action: 'click' }, 201),
    })
    mountAt('/admin/campaigns/new', '/admin/campaigns/new')
    await pick('Dove porta', 'Un link')
    await userEvent.type(screen.getByLabelText('Indirizzo del link'), LUMA)
    await pick('Stato del percorso', 'Manca solo il CV')
    // The template fills what the admin never wrote, and leaves the destination alone.
    expect(screen.getByLabelText('Oggetto')).toHaveValue(TEMPLATE.subject)
    expect(screen.getByLabelText('Testo del bottone')).toHaveValue(TEMPLATE.button_text)
    expect(screen.getByRole('combobox', { name: 'Dove porta' })).toHaveTextContent('Un link')
    expect(screen.getByLabelText('Indirizzo del link')).toHaveValue(LUMA)
    expect(screen.getByText('Ha cliccato il link')).toBeInTheDocument()
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    const created = calls.mock.calls.filter(([url, init]) => init?.method === 'POST' && String(url).endsWith('/api/hub/campaigns'))
    expect(created).toHaveLength(1)
    expect(JSON.parse(String(created[0]![1]!.body))).toMatchObject({
      subject: TEMPLATE.subject,
      text: TEMPLATE.text,
      button_text: TEMPLATE.button_text,
      button_meta: 'link',
      button_url: LUMA,
      action: 'click',
    })
  })

  it('says under the field why an `xn--` host that does not decode is refused (Greptile on #476)', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json(DRAFT),
    })
    mountAt('/admin/campaigns/new', '/admin/campaigns/new')
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await pick('Dove porta', 'Un link')
    await userEvent.type(screen.getByLabelText('Indirizzo del link'), 'https://xn--a.com/')
    expect(screen.getByLabelText('Indirizzo del link')).toHaveAccessibleDescription('Il link del bottone non è un indirizzo valido.')
    await new Promise((resolve) => setTimeout(resolve, 800))
    expect(patches(calls, 'c1')).toEqual([])
  })

  it('says a refusal of the address from the server under the field, and saves again once it changes', async () => {
    const REFUSED = 'https://lu.ma/rifiutato'
    const SENTENCE = 'Il link del bottone non è un indirizzo valido.'
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': (init) => {
        const body = JSON.parse(String(init!.body)) as Record<string, unknown>
        return body.button_url === REFUSED
          ? json({ detail: [{ loc: ['body', 'button_url'], msg: SENTENCE, type: 'value_error' }] }, 422)
          : json({ ...DRAFT, ...body })
      },
    })
    mountAt('/admin/campaigns/new', '/admin/campaigns/new')
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await pick('Dove porta', 'Un link')
    const field = screen.getByLabelText('Indirizzo del link')
    await userEvent.type(field, REFUSED)
    // The editor has no rule against it; the server has, and the field says so.
    await vi.waitFor(() => expect(field).toHaveAccessibleDescription(SENTENCE), SAVED)
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText(`Non salvata: ${SENTENCE}`)).toBeInTheDocument()
    await userEvent.clear(field)
    await userEvent.type(field, LUMA)
    expect(await screen.findByText(/Bozza salvata alle/, {}, SAVED)).toBeInTheDocument()
    expect(field).not.toHaveAccessibleDescription(SENTENCE)
    expect(patches(calls, 'c1').at(-1)).toMatchObject({ button_meta: 'link', button_url: LUMA })
  })

  it('takes the menu of actions away from a filtered list while it leads to a link', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, source: 'filters', journey_status: null, filters: { list: 'talents' } }, 201),
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
    const parent = { ...DRAFT, id: 'c0', nome: 'Casa', status: 'sent', button_meta: 'link', button_url: LUMA, action: 'click' }
    const list = { ...parent, id: 'c9', nome: 'Casa · riscrivi', status: 'draft', source: 'list', journey_status: null, segue_id: 'c0' }
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c9/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c9': () => json({ campaign: list, counts: COUNTS_EMPTY, recipients: [] }),
      'GET /api/hub/campaigns/c0': () => json({ campaign: parent, counts: COUNTS_EMPTY, recipients: [] }),
      'PATCH /api/hub/campaigns/c9': () => json({ ...list, button_url: `${LUMA}-2` }),
    })
    mountAt('/admin/campaigns/$id/edit', '/admin/campaigns/c9/edit')
    expect(await screen.findByLabelText('Indirizzo del link')).toHaveValue(LUMA)
    expect(await options('Dove porta')).toEqual(['Un link'])
    await userEvent.type(screen.getByLabelText('Indirizzo del link'), '-2')
    await vi.waitFor(() => expect(patches(calls, 'c9')).toHaveLength(1), SAVED)
    expect(patches(calls, 'c9')[0]).toEqual({
      nome: 'Casa · riscrivi',
      subject: DRAFT.subject,
      text: DRAFT.text,
      button_text: DRAFT.button_text,
      button_meta: 'link',
      button_url: `${LUMA}-2`,
    })
  })
})
