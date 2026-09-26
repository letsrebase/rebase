import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { fireEvent, render, screen, within } from '@testing-library/react'
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
/** A second template, its own `stato_percorso` and its own `azione`: switching from
 *  `TEMPLATE` to this one is a switch the saved `azione` has to show (fix 4, REB-472). */
const TEMPLATE2 = {
  stato_percorso: 'profilo_incompleto',
  etichetta: 'Profilo da completare',
  oggetto: 'Completa il profilo',
  testo: 'Ciao {nome},\n\ncompleta il profilo.',
  bottone_testo: 'Vai al profilo',
  bottone_meta: 'wizard',
  azione: 'scheda_completa',
}
const DRAFT = {
  id: 'c1', nome: 'Manca solo il CV', slug: 's', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null,
  oggetto: TEMPLATE.oggetto, testo: TEMPLATE.testo, bottone_testo: TEMPLATE.bottone_testo, bottone_meta: 'area', azione: 'cv',
  stato: 'bozza', contenuto_at: '2026-09-25T07:00:00Z', programmata_per: null, prova_inviata_at: null, inviata_at: null,
  created_at: '2026-09-25T07:00:00Z', pronta: false,
}
/** What the server answers once a test has left and nothing changed since. */
const TESTED = { ...DRAFT, pronta: true, prova_inviata_at: '2026-09-25T07:05:00Z' }
const AUDIENCE = {
  righe: [
    { email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', escluso: null },
    { email: 'ivan@rebase.it', nome: 'Ivan', tipo: 'freelancer', escluso: 'amministratore' },
  ],
  incluse: 1,
  escluse: 1,
}
const AUDIENCE_TWO = {
  righe: [
    { email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', escluso: null },
    { email: 'bruno@studio.it', nome: 'Bruno', tipo: 'freelancer', escluso: null },
    { email: 'ivan@rebase.it', nome: 'Ivan', tipo: 'freelancer', escluso: 'amministratore' },
  ],
  incluse: 2,
  escluse: 1,
}
const ME = { email: 'ivan@rebase.it', nome: 'Ivan', role: 'admin' }
const COUNTS_EMPTY = { destinatari: 0, in_coda: 0, inviate: 0, saltate: 0, fallite: 0, consegnate: 0, rimbalzate: 0 }

/** The draft saves itself 600 ms after the last change: anything that waits on a save
 *  waits longer than Testing Library's default second. */
const SAVED = { timeout: 3000 }

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Answers by method and path, and records every call for the assertions. The first
 *  registered prefix a request starts with wins, so a longer path (`c1/audience`,
 *  `c1/test`) goes before the shorter one it would otherwise be shadowed by. */
function api(routes: Record<string, () => Response | Promise<Response>>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const match = Object.keys(routes).find((prefix) => key.startsWith(prefix))
    return match ? routes[match]!() : json({ detail: `unexpected ${key}` }, 500)
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

const mount = () => mountAt('/admin/campaigns/new', '/admin/campaigns/new')
const mountEdit = (id: string) => mountAt('/admin/campaigns/$id/edit', `/admin/campaigns/${id}/edit`)

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

/** Opens the `Select` a label names and picks one of its options. */
async function pick(label: string, option: string) {
  await userEvent.click(await screen.findByRole('combobox', { name: label }))
  await userEvent.click(await screen.findByRole('option', { name: option }))
}

/** A date input takes its value whole, as a browser's picker hands it over. */
function day(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

type Calls = ReturnType<typeof api>

function bodies(calls: Calls, method: string, path: RegExp) {
  return calls.mock.calls
    .filter(([url, init]) => (init?.method ?? 'GET') === method && path.test(String(url)))
    .map(([, init]) => JSON.parse(String(init!.body)))
}

/** The draft as the server last received it, from the create or the latest patch. */
function lastSaved(calls: Calls) {
  const saves = calls.mock.calls.filter(
    ([url, init]) =>
      (init?.method === 'POST' && /\/api\/hub\/campaigns$/.test(String(url))) ||
      (init?.method === 'PATCH' && /\/api\/hub\/campaigns\/[^/]+$/.test(String(url))),
  )
  return JSON.parse(String(saves.at(-1)![1]!.body))
}

function sendBar() {
  return within(screen.getByRole('region', { name: 'Invio' }))
}

function preview() {
  return within(screen.getByTestId('anteprima-mail'))
}

/** Every Talenti filter filled, as the admin types it. */
const TALENTI_FILTRI = {
  lista: 'talenti',
  stato: 'attivo',
  q: 'react',
  posizione: 'Frontend',
  remoto: 'ibrido',
  tariffa_min: '300',
  tariffa_max: '500',
  origine: 'home',
  utm_source: 'linkedin',
  has_cv: true,
  con_accessi: false,
  creato_da: '2026-01-01',
  creato_a: '2026-09-01',
}

/** What the page sends for them, an amount as the lists send it since REB-485 (two
 *  decimals and a dot): the same literal `test_campaign_schemas.py` validates against the
 *  server's `TalentiFiltri`, so a renamed or retyped field fails on one side or the other. */
const TALENTI_SENT = { ...TALENTI_FILTRI, tariffa_min: '300.00', tariffa_max: '500.00' }

/** Every company filter filled, as the admin types it; `AZIENDE_SENT` below is what
 *  `test_campaign_schemas.py` validates against `AziendeFiltri`. */
const AZIENDE_FILTRI = {
  lista: 'aziende',
  stato: 'in_corso',
  q: 'block',
  budget_min: '200',
  budget_max: '400',
  periodo_da: '2026-10-01',
  origine: 'home',
  creato_da: '2026-01-01',
  creato_a: '2026-09-01',
}
const AZIENDE_SENT = { ...AZIENDE_FILTRI, budget_min: '200.00', budget_max: '400.00' }

describe('«Nuova campagna» on one page (REB-526)', () => {
  it('fills the mail from the state, saves it by itself, counts the list, and sends after a test', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns/c1/test': () => json(TESTED),
      'POST /api/hub/campaigns/c1/schedule': () => json({ ...TESTED, stato: 'programmata' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
    })
    mount()
    expect(await screen.findByText('Scegli uno stato per vedere chi riceve la mail.')).toBeInTheDocument()
    expect(sendBar().getByText('Scegli prima a chi scrivere.')).toBeInTheDocument()
    // No list yet: the button names no number rather than a zero, and no action is
    // measured before a state brings one.
    expect(sendBar().getByRole('button', { name: 'Invia' })).toBeDisabled()
    expect(screen.queryByText('Cosa misuriamo:')).not.toBeInTheDocument()
    await pick('Stato del percorso', 'Manca solo il CV')
    // The template fills the mail at once, and the preview reads it as the mail will.
    expect(screen.getByLabelText('Oggetto')).toHaveValue('Manca solo il CV')
    expect(screen.getByLabelText('Nome della campagna')).toHaveValue('Manca solo il CV')
    expect(screen.getByText('Ha caricato il CV')).toBeInTheDocument()
    // No «Avanti»: the draft is created by itself and the list follows.
    expect(await screen.findByText('riceverà la mail', { exact: false }, SAVED)).toBeInTheDocument()
    expect(screen.getByText(/1 escluse dalle regole/)).toBeInTheDocument()
    expect(preview().getByText('Ciao Ada,')).toBeInTheDocument()
    expect(bodies(calls, 'POST', /\/api\/hub\/campaigns$/)).toEqual([
      {
        nome: 'Manca solo il CV',
        fonte: 'stato',
        stato_percorso: 'manca_cv',
        filtri: null,
        oggetto: TEMPLATE.oggetto,
        testo: TEMPLATE.testo,
        bottone_testo: TEMPLATE.bottone_testo,
        bottone_meta: 'area',
        azione: 'cv',
      },
    ])
    await userEvent.click(screen.getByRole('button', { name: /Mostra l.elenco \(2\)/ }))
    expect(screen.getByText('amministratore')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'ivan@rebase.it' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'ivan@rebase.it' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'ada@studio.it' })).toBeChecked()
    // The button names the count; without a test it is off and the bar says why.
    expect(sendBar().getByRole('button', { name: 'Invia a 1 persona' })).toBeDisabled()
    expect(sendBar().getByText('Manda prima una prova: il bottone è sotto l’anteprima.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    expect(await screen.findByText('Prova inviata alle 09:05 a ivan@rebase.it. Puoi inviare.')).toBeInTheDocument()
    await userEvent.click(sendBar().getByRole('button', { name: 'Invia a 1 persona' }))
    expect(await screen.findByText('pagina campagna')).toBeInTheDocument()
    expect(bodies(calls, 'POST', /\/schedule$/)).toEqual([{ esclusi: [] }])
    expect(bodies(calls, 'POST', /\/api\/hub\/campaigns$/)).toHaveLength(1)
  })

  it('takes an unticked person out of the count, the preview and the send', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE_TWO),
      'POST /api/hub/campaigns/c1/test': () => json(TESTED),
      'POST /api/hub/campaigns/c1/schedule': () => json({ ...TESTED, stato: 'programmata' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    expect(await screen.findByText('riceveranno la mail', { exact: false }, SAVED)).toBeInTheDocument()
    expect(preview().getByText('Ciao Ada,')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Mostra l.elenco \(3\)/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'ada@studio.it' }))
    expect(screen.getByText(/1 tolte da te/)).toBeInTheDocument()
    expect(preview().getByText('Ciao Bruno,')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    await userEvent.click(await sendBar().findByRole('button', { name: 'Invia a 1 persona' }))
    await screen.findByText('pagina campagna')
    expect(bodies(calls, 'POST', /\/schedule$/)).toEqual([{ esclusi: ['ada@studio.it'] }])
  })

  it('forgets the unticks when another state brings another list', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE, TEMPLATE2]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE_TWO),
      'POST /api/hub/campaigns/c1/test': () => json({ ...TESTED, stato_percorso: 'profilo_incompleto', azione: 'scheda_completa' }),
      'POST /api/hub/campaigns/c1/schedule': () => json({ ...TESTED, stato: 'programmata' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, stato_percorso: 'profilo_incompleto', azione: 'scheda_completa' }),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceveranno la mail', { exact: false }, SAVED)
    await userEvent.click(screen.getByRole('button', { name: /Mostra l.elenco/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'ada@studio.it' }))
    expect(screen.getByText(/1 tolte da te/)).toBeInTheDocument()
    await pick('Stato del percorso', 'Profilo da completare')
    expect(screen.queryByText(/tolte da te/)).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'ada@studio.it' })).toBeChecked()
    await vi.waitFor(() => expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/)).toHaveLength(1), SAVED)
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    await userEvent.click(await sendBar().findByRole('button', { name: 'Invia a 2 persone' }, SAVED))
    await screen.findByText('pagina campagna')
    expect(bodies(calls, 'POST', /\/schedule$/)).toEqual([{ esclusi: [] }])
  })

  it('keeps a typed name and still fills the mail from the state picked after it', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE, TEMPLATE2]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, nome: 'CV di settembre' }, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, nome: 'CV di settembre' }),
    })
    mount()
    await userEvent.type(await screen.findByLabelText('Nome della campagna'), 'CV di settembre')
    await pick('Stato del percorso', 'Manca solo il CV')
    expect(screen.getByLabelText('Nome della campagna')).toHaveValue('CV di settembre')
    expect(screen.getByLabelText('Oggetto')).toHaveValue('Manca solo il CV')
    // Until the admin writes into the mail, another state still rewrites it.
    await pick('Stato del percorso', 'Profilo da completare')
    expect(screen.getByLabelText('Oggetto')).toHaveValue('Completa il profilo')
    expect(screen.getByLabelText('Nome della campagna')).toHaveValue('CV di settembre')
  })

  it('shows another person on request in the preview', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE_TWO),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceveranno la mail', { exact: false }, SAVED)
    await pick('Vedi come la riceve', 'Bruno')
    expect(preview().getByText('Ciao Bruno,')).toBeInTheDocument()
  })

  it('turns «Invia» off as soon as the mail changes after the test', async () => {
    let tested = false
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns/c1/test': () => ((tested = true), json(TESTED)),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      // What the server answers to an edit after a test: the test is still on record
      // (`prova_inviata_at`), just older than the edit, so `pronta` is false.
      'PATCH /api/hub/campaigns/c1': () => json({ ...TESTED, oggetto: 'Manca solo il CV!', pronta: !tested }),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    expect(await sendBar().findByRole('button', { name: 'Invia a 1 persona' })).toBeEnabled()
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    // Before the save lands: the page already knows the test no longer holds.
    expect(sendBar().getByRole('button', { name: 'Invia a 1 persona' })).toBeDisabled()
    expect(screen.getByText('Hai cambiato la campagna dopo la prova delle 09:05: mandane un’altra.')).toBeInTheDocument()
    expect(
      await sendBar().findByText('Hai cambiato la campagna dopo la prova: mandane un’altra.', {}, SAVED),
    ).toBeInTheDocument()
    expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/).at(-1).oggetto).toBe('Manca solo il CV!')
  })

  it('holds a save asked for while the test is out until the test has answered', async () => {
    let answerTest: (response: Response) => void = () => undefined
    const order: string[] = []
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns/c1/test': () => (order.push('test'), new Promise<Response>((resolve) => (answerTest = resolve))),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => (order.push('patch'), json({ ...TESTED, oggetto: 'Manca solo il CV!', pronta: false })),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    await new Promise((resolve) => setTimeout(resolve, 900))
    expect(order).toEqual(['test'])
    answerTest(json(TESTED))
    await vi.waitFor(() => expect(order).toEqual(['test', 'patch']), SAVED)
    // The patch answered last, so the page reads its verdict, not the test's.
    expect(await sendBar().findByText('Hai cambiato la campagna dopo la prova: mandane un’altra.', {}, SAVED)).toBeInTheDocument()
  })

  it('never saves back a draft older than the one the test has just saved', async () => {
    let patches = 0
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns/c1/test': () => json({ ...TESTED, oggetto: 'Manca solo il CV!?' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () =>
        (patches += 1) === 1 ? json({ detail: 'Riprova.' }, 503) : json({ ...DRAFT, oggetto: 'Manca solo il CV!?' }),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    await screen.findByText('Non salvata: Riprova.', {}, SAVED)
    // Another keystroke and the test at once, before the draft settles again.
    await userEvent.type(screen.getByLabelText('Oggetto'), '?')
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    await screen.findByText(/Prova inviata alle/)
    await new Promise((resolve) => setTimeout(resolve, 900))
    expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/).map((body) => body.oggetto)).toEqual([
      'Manca solo il CV!',
      'Manca solo il CV!?',
    ])
    expect(sendBar().getByRole('button', { name: 'Invia a 1 persona' })).toBeEnabled()
  })

  it('saves a burst of typing once, and patches the campaign it created', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, testo: `${TEMPLATE.testo} Grazie.` }),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await userEvent.type(screen.getByLabelText('Testo'), ' Grazie.')
    expect(preview().getByText(/manca il CV\. Grazie\./)).toBeInTheDocument()
    expect(await screen.findByText(/Bozza salvata alle/, {}, SAVED)).toBeInTheDocument()
    await vi.waitFor(() => expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/)).toHaveLength(1), SAVED)
    expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/)[0].testo).toBe(`${TEMPLATE.testo} Grazie.`)
    expect(bodies(calls, 'POST', /\/api\/hub\/campaigns$/)).toHaveLength(1)
  })

  it('says a failed save in the header and in the send bar', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ detail: 'La campagna non è più una bozza.' }, 409),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    expect(await screen.findByText('Non salvata: La campagna non è più una bozza.', {}, SAVED)).toBeInTheDocument()
    expect(sendBar().getByText('Le modifiche non sono salvate: La campagna non è più una bozza.')).toBeInTheDocument()
    // Undoing the edit brings the form back to what the server holds: nothing unsaved.
    await userEvent.type(screen.getByLabelText('Oggetto'), '{Backspace}')
    expect(await screen.findByText(/Bozza salvata alle/, {}, SAVED)).toBeInTheDocument()
    expect(screen.queryByText(/Non salvata/)).not.toBeInTheDocument()
  })

  it('says why the list is missing when the first save or the list fails', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json({ detail: 'Filtro non valido.' }, 422),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    expect(await screen.findByRole('alert', {}, SAVED)).toHaveTextContent('Filtro non valido.')
    expect(sendBar().getByText('L’elenco non si carica: Filtro non valido.')).toBeInTheDocument()
  })

  it('shows a failed create where the list would be', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns': () => json({ detail: 'Stato del percorso sconosciuto.' }, 422),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    expect(await screen.findByText('Non salvata: Stato del percorso sconosciuto.', {}, SAVED)).toBeInTheDocument()
    expect(screen.queryByText('Carico l’elenco…')).not.toBeInTheDocument()
    expect(screen.getAllByText('Stato del percorso sconosciuto.', { exact: false }).length).toBeGreaterThan(1)
  })

  it('stops each field at the server\'s limit, and the preview reads like the mail', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    expect(screen.getByLabelText('Nome della campagna')).toHaveAttribute('maxLength', '120')
    expect(screen.getByLabelText('Oggetto')).toHaveAttribute('maxLength', '200')
    expect(screen.getByLabelText('Testo')).toHaveAttribute('maxLength', '5000')
    expect(screen.getByLabelText('Testo del bottone')).toHaveAttribute('maxLength', '60')
    const footer = preview().getByText(
      (_, node) => node?.tagName === 'P' && node.textContent === 'Non vuoi più ricevere queste mail? Disiscriviti',
    )
    expect(footer).toBeInTheDocument()
    expect(preview().getByText('Carica il CV')).toBeInTheDocument()
    expect(preview().getByText((_, node) => node?.tagName === 'P' && node.textContent === 'Ivan\nrebase')).toBeInTheDocument()
    // Before the list is in, the greeting drops the name as the server does.
    expect(preview().getByText('Ciao,')).toBeInTheDocument()
  })

  it('proposes an hour ahead when «Programma» is chosen, and says it on the button', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-09-25T07:00:00Z'))
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns/c1/test': () => json(TESTED),
      'POST /api/hub/campaigns/c1/schedule': () => json({ ...TESTED, stato: 'programmata' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    await screen.findByText(/Prova inviata alle/)
    // Two hours after the page opened: the proposal is from the click, not the mount.
    vi.setSystemTime(new Date('2026-09-25T09:07:00Z'))
    await userEvent.click(sendBar().getByRole('button', { name: 'Programma' }))
    expect(screen.getByLabelText('Giorno')).toHaveValue('2026-09-25')
    expect(screen.getByLabelText('Ora')).toHaveValue('12:15') // 11:07 in Rome, + 1 h, next quarter
    await userEvent.click(sendBar().getByRole('button', { name: 'Programma per 1 persona, ven 25 set, 12:15' }))
    await screen.findByText('pagina campagna')
    expect(bodies(calls, 'POST', /\/schedule$/)).toEqual([{ giorno: '2026-09-25', ora: '12:15', esclusi: [] }])
  })
})

describe('filters (REB-472, carried over)', () => {
  it('saves a filtered campaign under the default name, with its filters', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c2/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () =>
        json({ ...DRAFT, id: 'c2', nome: 'Campagna da filtri', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c2': () =>
        json({ ...DRAFT, id: 'c2', nome: 'Campagna da filtri', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', stato: 'nuovo' } }),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await pick('Stato', 'Nuovo')
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await vi.waitFor(() => expect(lastSaved(calls).filtri).toEqual({ lista: 'talenti', stato: 'nuovo' }), SAVED)
    expect(lastSaved(calls).nome).toBe('Campagna da filtri')
    expect(lastSaved(calls).fonte).toBe('filtri')
  })

  it('reloads the list when a filter changes, and says it is updating meanwhile', async () => {
    let audienceCalls = 0
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c3/audience': () => ((audienceCalls += 1), json(AUDIENCE)),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, id: 'c3', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c3': () =>
        json({ ...DRAFT, id: 'c3', fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', q: 'ada' } }),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    expect(audienceCalls).toBe(1)
    await userEvent.type(screen.getByLabelText('Cerca'), 'ada')
    expect(screen.getByText(/aggiorno l.elenco/)).toBeInTheDocument()
    await vi.waitFor(() => expect(audienceCalls).toBe(2), SAVED)
    await vi.waitFor(() => expect(screen.queryByText(/aggiorno l.elenco/)).not.toBeInTheDocument())
  })

  it('says the list is updating when the state filter changes', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', stato: 'lead' } }),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await screen.findByText('riceverà la mail', { exact: false }, SAVED)
    await pick('Stato', 'Lead')
    expect(screen.getByText(/aggiorno l.elenco/)).toBeInTheDocument()
    expect(sendBar().getByRole('button', { name: /Invia a/ })).toBeDisabled()
  })

  it('keeps the rarer filters behind «Altri filtri»', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    expect(screen.getByLabelText('Ha un CV')).toBeInTheDocument()
    expect(screen.queryByLabelText('Posizione')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Altri filtri' }))
    expect(screen.getByLabelText('Posizione')).toBeInTheDocument()
  })

  it('sends every Talenti filter under the server\'s own name and type', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: TALENTI_FILTRI }),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await userEvent.click(screen.getByRole('button', { name: 'Altri filtri' }))
    await pick('Stato', 'Attivo')
    await userEvent.type(screen.getByLabelText('Cerca'), 'react')
    await userEvent.type(screen.getByLabelText('Posizione'), 'Frontend')
    await pick('Da remoto', 'Ibrido')
    await userEvent.type(screen.getByLabelText('Tariffa min (€/giorno)'), '300')
    await userEvent.type(screen.getByLabelText('Tariffa max (€/giorno)'), '500')
    await userEvent.type(screen.getByLabelText('Pagina di provenienza'), 'home')
    await userEvent.type(screen.getByLabelText('UTM source'), 'linkedin')
    await pick('Ha un CV', 'Sì')
    await pick('Ha fatto accesso', 'No')
    day('Creato dal', '2026-01-01')
    day('Creato al', '2026-09-01')
    await vi.waitFor(() => expect(lastSaved(calls).filtri).toEqual(TALENTI_SENT), SAVED)
  })

  it('sends every company filter under the server\'s own name and type', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: AZIENDE_FILTRI }),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await pick('Lista', 'Aziende')
    await userEvent.click(screen.getByRole('button', { name: 'Altri filtri' }))
    await pick('Stato', 'In corso')
    await userEvent.type(screen.getByLabelText('Cerca'), 'block')
    await userEvent.type(screen.getByLabelText('Budget min (€/giorno)'), '200')
    await userEvent.type(screen.getByLabelText('Budget max (€/giorno)'), '400')
    day('Periodo dal', '2026-10-01')
    await userEvent.type(screen.getByLabelText('Pagina di provenienza'), 'home')
    day('Creata dal', '2026-01-01')
    day('Creata al', '2026-09-01')
    await vi.waitFor(() => expect(lastSaved(calls).filtri).toEqual(AZIENDE_SENT), SAVED)
  })

  it('reads a day rate the Italian way, as the lists do (REB-485), and drops one that is not an amount', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti' } }),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Filtri' }))
    await userEvent.click(screen.getByRole('button', { name: 'Altri filtri' }))
    await userEvent.type(screen.getByLabelText('Tariffa min (€/giorno)'), '1.500')
    await userEvent.type(screen.getByLabelText('Tariffa max (€/giorno)'), 'tanto')
    expect(screen.getByText('Serve una cifra, in euro.')).toBeInTheDocument()
    await vi.waitFor(() => expect(lastSaved(calls).filtri).toEqual({ lista: 'talenti', tariffa_min: '1500.00' }), SAVED)
  })

  it('draws the list with the shared Table primitive', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
    })
    mount()
    await pick('Stato del percorso', 'Manca solo il CV')
    await userEvent.click(await screen.findByRole('button', { name: /Mostra l.elenco/ }, SAVED))
    const table = screen.getByRole('table')
    expect(table.closest('[data-slot="table-container"]')).not.toBeNull()
  })
})

describe('the edit route', () => {
  it('opens a tested draft ready to send, and saves nothing just for opening it', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c1': () => json({ campagna: TESTED, conteggi: COUNTS_EMPTY, destinatari: [] }),
    })
    mountEdit('c1')
    expect(await screen.findByRole('heading', { name: 'Modifica campagna' })).toBeInTheDocument()
    expect(screen.getByLabelText('Oggetto')).toHaveValue('Manca solo il CV')
    expect(await sendBar().findByRole('button', { name: 'Invia a 1 persona' }, SAVED)).toBeEnabled()
    await new Promise((resolve) => setTimeout(resolve, 800))
    expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/)).toEqual([])
  })

  it('reads the list from filtri.lista, not from which fields are present', async () => {
    const editCampaign = { ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', stato: 'nuovo' } }
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c1': () => json({ campagna: editCampaign, conteggi: COUNTS_EMPTY, destinatari: [] }),
    })
    mountEdit('c1')
    // Talenti-only field: only present once the seeded `lista` really reads 'talenti'.
    expect(await screen.findByLabelText('Ha un CV')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Stato' })).toHaveTextContent('Nuovo')
  })

  it('puts every stored filter back, «Altri filtri» open, and sends them back unchanged', async () => {
    const stored = { ...TALENTI_FILTRI, creato_da: '2026-01-01T00:00:00', creato_a: '2026-09-01T00:00:00' }
    const editCampaign = { ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: stored }
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c1': () => json({ campagna: editCampaign, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'PATCH /api/hub/campaigns/c1': () => json(editCampaign),
    })
    mountEdit('c1')
    expect(await screen.findByLabelText('Creato dal')).toHaveValue('2026-01-01')
    expect(screen.getByRole('combobox', { name: 'Da remoto' })).toHaveTextContent('Ibrido')
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    await vi.waitFor(() => expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/)).toHaveLength(1), SAVED)
    expect(lastSaved(calls).filtri).toEqual(TALENTI_SENT)
  })

  it('reads a stored amount as the decimal the server wrote, never as Italian thousands', async () => {
    // «1.500» typed into REB-472's number input was 1.5, and the server kept the string.
    const editCampaign = { ...DRAFT, fonte: 'filtri', stato_percorso: null, filtri: { lista: 'talenti', tariffa_min: '1.500' } }
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c1': () => json({ campagna: editCampaign, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'PATCH /api/hub/campaigns/c1': () => json(editCampaign),
    })
    mountEdit('c1')
    expect(await screen.findByLabelText('Tariffa min (€/giorno)')).toHaveValue('1.50')
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    await vi.waitFor(() => expect(bodies(calls, 'PATCH', /\/campaigns\/c1$/)).toHaveLength(1), SAVED)
    expect(lastSaved(calls).filtri).toEqual({ lista: 'talenti', tariffa_min: '1.50' })
  })

  it('follows a new state with the action, and keeps the mail the admin wrote', async () => {
    const editCampaign = { ...DRAFT, id: 'c4' }
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE, TEMPLATE2]),
      'GET /api/hub/campaigns/c4/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c4': () => json({ campagna: editCampaign, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'PATCH /api/hub/campaigns/c4': () =>
        json({ ...editCampaign, stato_percorso: 'profilo_incompleto', azione: 'scheda_completa' }),
    })
    mountEdit('c4')
    await pick('Stato del percorso', 'Profilo da completare')
    expect(screen.getByText('Ha completato la scheda')).toBeInTheDocument()
    expect(screen.getByLabelText('Oggetto')).toHaveValue('Manca solo il CV')
    await vi.waitFor(() => expect(bodies(calls, 'PATCH', /\/campaigns\/c4$/)).toHaveLength(1), SAVED)
    expect(lastSaved(calls).azione).toBe('scheda_completa')
    expect(lastSaved(calls).stato_percorso).toBe('profilo_incompleto')
  })

  it('refuses to edit a campaign that has left «bozza»', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c1': () =>
        json({ campagna: { ...TESTED, stato: 'programmata' }, conteggi: COUNTS_EMPTY, destinatari: [] }),
    })
    mountEdit('c1')
    expect(await screen.findByText(/non è più una bozza, quindi non si modifica/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Oggetto')).not.toBeInTheDocument()
  })
})
