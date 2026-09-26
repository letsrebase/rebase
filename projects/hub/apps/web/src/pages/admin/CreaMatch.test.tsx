import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useLocation,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LETTERA_TEXT_KEYS } from '@/lib/api'
import { AdminCreaMatch } from './CreaMatch'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

type Handler = unknown
/** Routes by `"<method> <url>"`; a handler may be a function of the request and may
 *  answer a whole `Response` (the previews are PDFs, not JSON), or a promise of either
 *  (a response the test holds back and lets land later). */
function routeFetch(handlers: Record<string, Handler>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    if (!(key in handlers)) throw new Error(`unhandled fetch in this test: ${key}`)
    const handler = handlers[key]
    const body = await (typeof handler === 'function' ? (handler as (init?: RequestInit) => unknown)(init) : handler)
    return body instanceof Response ? body : answer(200, body)
  })
}

/** A response the test answers when it chooses, to land one out of order. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

/** The JSON bodies sent to one route. */
function bodies(spy: ReturnType<typeof routeFetch>, method: string, url: string) {
  return spy.mock.calls
    .filter(([input, init]) => String(input) === url && (init?.method ?? 'GET') === method)
    .map(([, init]) => JSON.parse(String(init!.body)))
}

const pdf = () => new Response('%PDF-1.7 anteprima', { status: 200, headers: { 'Content-Type': 'application/pdf' } })

const PERSON = { id: 'f1', nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it' }
const OPEN = {
  id: 'c1',
  nome_azienda: 'Rossi Studio',
  referente: 'Mario Rossi',
  email: 'mario@rossi.it',
  telefono: null,
  figura_richiesta: 'Backend developer',
  progetto: 'Piattaforma di prenotazione',
  periodo_da: '2026-10-01',
  durata: '3 mesi',
  budget_giornaliero: '777.77',
  remoto: 'remoto',
  giorni_presenza: null,
  numero_risorse: 1,
  stato: 'nuovo',
  note: null,
  origine: null,
  utm_source: null,
  created_at: '2026-09-10T10:00:00Z',
  commenti: [],
  deleted_at: null,
}
const CLOSED = { ...OPEN, id: 'c2', nome_azienda: 'Bianchi Srl', stato: 'chiuso' }
const VERDI = { ...OPEN, id: 'c9', nome_azienda: 'Verdi Snc', referente: 'Luca Verdi' }
const FISCALE = {
  freelancer_id: 'f1',
  codice_fiscale: 'LVLDAA85T50H501Z',
  partita_iva: '01234567890',
  domicilio: 'Via Roma 1, Milano',
  pec: null,
  updated_by: 'a1',
  updated_at: '2026-09-23T10:00:00Z',
}
const ROSSI_CLIENTE = { cliente_ragione_sociale: 'Rossi Studio', cliente_piva: '09876543210', cliente_sede: 'Milano' }

function prefill({
  compenso = '450.00' as string | null,
  cliente = ROSSI_CLIENTE as Record<string, string | null>,
  fiscale = FISCALE as typeof FISCALE | null,
} = {}) {
  return {
    fiscale,
    cliente,
    lettera: {
      ...Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, null])),
      ruolo: 'Backend developer',
      attivita: 'Piattaforma di prenotazione',
      impegno: '3 mesi',
      luogo: 'da remoto',
      referente_cliente: 'Mario Rossi',
      modalita: 'a giornata',
      unita: 'a giornata',
      data_inizio: '2026-10-01',
      data_fine: null,
      compenso,
      giorni_pagamento: 30,
      fine_mese: true,
      giorni_preavviso: null,
    },
    quadro_attivo: null,
    quadro_necessario: true,
    lettera_in_attesa: true,
  }
}

const CHECK = {
  riepilogo: [
    'Ada Lovelace lavorerà per Rossi Studio come Backend developer, da remoto, dal 1 ottobre 2026.',
    'Impegno: 3 mesi.',
    'Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese.',
  ],
  cosa_succede: 'Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma.',
  quadro_necessario: true,
  dati_fiscali_mancanti: false,
}
const CHECK_NO_QUADRO = {
  ...CHECK,
  cosa_succede: 'Il contratto quadro è già attivo: parte subito la lettera di incarico.',
  quadro_necessario: false,
}

/** Everything a walk from a request to a saved draft reads, each test adding or
 *  replacing what it is about. */
function routes(extra: Record<string, Handler> = {}) {
  return routeFetch({
    'GET /api/hub/freelancers/f1': PERSON,
    'GET /api/hub/companies?limit=50': { totale: 3, items: [OPEN, VERDI, CLOSED], per_stato: {}, next_cursor: null },
    'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill(),
    'GET /api/hub/freelancers/f1/matches/prefill?company_id=c9': prefill({
      compenso: '900.00',
      cliente: { cliente_ragione_sociale: 'Verdi Snc', cliente_piva: '11122233344', cliente_sede: 'Torino' },
    }),
    'POST /api/hub/freelancers/f1/matches/check': CHECK,
    'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
    'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': pdf,
    'POST /api/hub/freelancers/f1/matches': { id: 'm1' },
    ...extra,
  })
}

/** «Match e contratti» as far as this page is concerned: where it lands, and the
 *  sentence it carries there in the history state. */
function ContrattiStub() {
  const notice = useLocation({ select: (location) => location.state.notice })
  return (
    <>
      <p>pagina contratti</p>
      {notice && <p data-testid="notice">{notice}</p>}
    </>
  )
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const nuovo = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/match/new',
    component: AdminCreaMatch,
  })
  const contratti = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: ContrattiStub,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([nuovo, contratti])]),
    history: createMemoryHistory({ initialEntries: ['/admin/freelance/f1/match/new'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

let made = 0
beforeEach(() => {
  made = 0
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => `blob:anteprima-${++made}`) })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
})
afterEach(() => vi.restoreAllMocks())

const current = (label: string) => expect(screen.getByText(label)).toHaveAttribute('aria-current', 'step')

async function pick(name: RegExp) {
  await userEvent.click(await screen.findByRole('button', { name }))
  await screen.findByRole('heading', { name: 'Cliente sulla lettera' })
}

async function toCondizioni() {
  await pick(/Rossi Studio/)
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
  await screen.findByLabelText('Ruolo')
}

async function toControlla() {
  await toCondizioni()
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
  await screen.findByRole('button', { name: 'Salva senza inviare' })
}

describe('«Crea match» in three steps (REB-476)', () => {
  it('walks the three steps from a request to a saved draft and lands on «Match e contratti» saying so', async () => {
    const spy = routes()
    mount()
    expect(await screen.findByRole('button', { name: /Bianchi Srl/ })).toBeDisabled()
    current('1. Chi e per chi')
    expect(screen.getByText('2. Condizioni')).toBeInTheDocument()
    expect(screen.getByText('3. Controlla e invia')).toBeInTheDocument()

    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Ruolo')).toHaveValue('Backend developer')
    current('2. Condizioni')
    expect(screen.getByRole('heading', { name: 'Passo 2 di 3: Condizioni' })).toHaveFocus()

    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('button', { name: 'Salva senza inviare' })).toBeEnabled()
    current('3. Controlla e invia')
    expect(screen.getByRole('heading', { name: 'Passo 3 di 3: Controlla e invia' })).toHaveFocus()

    await userEvent.click(screen.getByRole('button', { name: 'Salva senza inviare' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    expect(screen.getByTestId('notice')).toHaveTextContent('Bozza salvata: la trovi qui sotto, da inviare.')
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches')
    expect(body.company_id).toBe('c1')
    expect(body.cliente).toEqual(ROSSI_CLIENTE)
    expect(body.lettera).toMatchObject({
      ruolo: 'Backend developer',
      attivita: 'Piattaforma di prenotazione',
      modalita: 'a giornata',
      unita: 'a giornata',
      compenso: '450',
      giorni_pagamento: 30,
      fine_mese: true,
      risultati: null,
    })
  })

  it('never shows or sends the request’s budget', async () => {
    const spy = routes()
    mount()
    await pick(/Rossi Studio/)
    expect(document.body.textContent).not.toMatch(/777|budget/i)
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(document.body.innerHTML).not.toMatch(/777|budget/i)
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    expect(document.body.innerHTML).not.toMatch(/777|budget/i)
    const sent = spy.mock.calls.map(([, init]) => String(init?.body ?? '')).join('\n')
    expect(sent).not.toMatch(/777|budget/i)
  })
})

describe('step 1, «Chi e per chi»', () => {
  it('shows a complete client as one line with «Modifica», and its fields once opened', async () => {
    const spy = routes()
    mount()
    await pick(/Rossi Studio/)
    expect(screen.getByText('Rossi Studio · P.IVA 09876543210 · Milano')).toBeInTheDocument()
    expect(screen.queryByLabelText('Ragione sociale del cliente')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Modifica il cliente' }))
    expect(screen.getByLabelText('Ragione sociale del cliente')).toHaveValue('Rossi Studio')
    expect(screen.getByLabelText('Ragione sociale del cliente')).toHaveFocus()
    const sede = screen.getByLabelText('Sede del cliente')
    await userEvent.clear(sede)
    await userEvent.type(sede, 'Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(body.cliente.cliente_sede).toBe('Torino')
  })

  it('asks for the client’s missing details in their three fields', async () => {
    routes({
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill({
        cliente: { cliente_ragione_sociale: 'Rossi Studio', cliente_piva: null, cliente_sede: null },
      }),
    })
    mount()
    await pick(/Rossi Studio/)
    expect(screen.getByLabelText('Ragione sociale del cliente')).toHaveValue('Rossi Studio')
    expect(screen.getByLabelText('Partita IVA del cliente')).toHaveValue('')
    expect(screen.getByLabelText('Partita IVA del cliente')).toBeRequired()
    expect(screen.getByLabelText('Sede del cliente')).toBeRequired()
    expect(screen.queryByRole('button', { name: 'Modifica il cliente' })).toBeNull()
  })

  it('does not save tax data it only showed', async () => {
    const spy = routes()
    mount()
    await pick(/Rossi Studio/)
    expect(screen.getByRole('heading', { name: 'Dati fiscali di Ada' })).toBeInTheDocument()
    expect(screen.getByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')).toBeInTheDocument()
    expect(screen.queryByLabelText('Codice fiscale')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(spy.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('does not save tax data opened with «Modifica» and left as they were', async () => {
    const spy = routes()
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('LVLDAA85T50H501Z')
    expect(screen.getByLabelText('Codice fiscale')).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(spy.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('saves tax data edited through «Modifica»', async () => {
    const spy = routes({ 'PUT /api/hub/freelancers/f1/fiscal': { ...FISCALE, domicilio: 'Via Po 2, Torino' } })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 2, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toEqual([
      { codice_fiscale: 'LVLDAA85T50H501Z', partita_iva: '01234567890', domicilio: 'Via Po 2, Torino', pec: null },
    ])

    // Back on the step, the data just saved are one line again, and not saved twice.
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(await screen.findByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toHaveLength(1)
  })

  it('asks for missing tax data, required, and saves them', async () => {
    const spy = routes({
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill({ fiscale: null }),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
    })
    mount()
    await pick(/Rossi Studio/)
    expect(screen.getByText('Mancano: servono per il contratto.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifica i dati fiscali' })).toBeNull()
    expect(screen.getByLabelText('Codice fiscale')).toBeRequired()
    expect(screen.getByLabelText('Partita IVA')).toBeRequired()
    expect(screen.getByLabelText('Domicilio professionale')).toBeRequired()
    await userEvent.type(screen.getByLabelText('Codice fiscale'), 'LVLDAA85T50H501Z')
    await userEvent.type(screen.getByLabelText('Partita IVA'), '01234567890')
    await userEvent.type(screen.getByLabelText('Domicilio professionale'), 'Via Roma 1, Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toEqual([
      { codice_fiscale: 'LVLDAA85T50H501Z', partita_iva: '01234567890', domicilio: 'Via Roma 1, Milano', pec: null },
    ])
  })

  it('drops a prefill that lands after the admin picked another company (Greptile 4092036036)', async () => {
    const late = deferred<Response>()
    routes({ 'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': () => late.promise })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await pick(/Verdi Snc/)
    late.resolve(answer(200, prefill()))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Avanti' })).toBeEnabled())
    expect(screen.getByText(/^Verdi Snc · Luca Verdi · Backend developer/)).toBeInTheDocument()
    expect(screen.getByText('Verdi Snc · P.IVA 11122233344 · Torino')).toBeInTheDocument()
    expect(screen.queryByText(/Rossi Studio · P\.IVA/)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('900')
  })

  it('keeps edited conditions after going back with the same company (REB-402)', async () => {
    const spy = routes()
    mount()
    await toCondizioni()
    const ruolo = screen.getByLabelText('Ruolo')
    await userEvent.clear(ruolo)
    await userEvent.type(ruolo, 'Ruolo modificato')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    current('1. Chi e per chi')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Ruolo')).toHaveValue('Ruolo modificato')
    expect(spy.mock.calls.filter(([url]) => String(url).includes('/matches/prefill'))).toHaveLength(1)
  })

  it('reloads the prefill when the company changes after going back (REB-402)', async () => {
    routes()
    mount()
    await toCondizioni()
    expect(screen.getByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('450')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await userEvent.click(await screen.findByRole('button', { name: /Verdi Snc/ }))
    expect(await screen.findByText('Verdi Snc · P.IVA 11122233344 · Torino')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('900')
  })

  it('reaches a request beyond the first page through «Mostra altre» (Greptile 4092036042)', async () => {
    const OLDER = { ...OPEN, id: 'c7', nome_azienda: 'Neri Spa', referente: 'Anna Neri', created_at: '2026-01-10T10:00:00Z' }
    routes({
      'GET /api/hub/companies?limit=50': { totale: 51, items: [OPEN], per_stato: {}, next_cursor: 'p2' },
      'GET /api/hub/companies?limit=50&cursor=p2': { totale: 51, items: [OLDER], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c7': prefill({
        cliente: { cliente_ragione_sociale: 'Neri Spa', cliente_piva: null, cliente_sede: null },
      }),
    })
    mount()
    await screen.findByRole('button', { name: /Rossi Studio/ })
    expect(screen.queryByRole('button', { name: /Neri Spa/ })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Mostra altre' }))
    await pick(/Neri Spa/)
    expect(screen.getByText(/^Neri Spa · Anna Neri · Backend developer/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mostra altre' })).toBeNull()
    expect(screen.getByLabelText('Ragione sociale del cliente')).toHaveValue('Neri Spa')
  })

  it('folds the list into the chosen request, and «Cambia richiesta» brings it back', async () => {
    const spy = routes()
    mount()
    await pick(/Rossi Studio/)
    expect(screen.queryByLabelText('Cerca una richiesta')).toBeNull()
    expect(screen.queryByRole('button', { name: /Verdi Snc/ })).toBeNull()
    expect(screen.getByText('Rossi Studio · Mario Rossi · Backend developer · dal 1 ott 2026')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cambia richiesta' })).toHaveFocus()

    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    expect(screen.getByLabelText('Cerca una richiesta')).toHaveFocus()
    expect(await screen.findByRole('button', { name: /Rossi Studio/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('heading', { name: 'Cliente sulla lettera' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Avanti' })).toBeDisabled()

    // The same request again folds the list back without reading it twice.
    await pick(/Rossi Studio/)
    expect(screen.getByRole('button', { name: 'Avanti' })).toBeEnabled()
    expect(spy.mock.calls.filter(([url]) => String(url).includes('/matches/prefill'))).toHaveLength(1)
  })

  it('reads the request again when picked again after a failed read', async () => {
    let reads = 0
    routes({
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': () =>
        ++reads === 1 ? answer(503, { detail: 'Il database non risponde.' }) : prefill(),
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Il database non risponde.')
    expect(screen.getByLabelText('Cerca una richiesta')).toBeInTheDocument()
    await pick(/Rossi Studio/)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('Rossi Studio · P.IVA 09876543210 · Milano')).toBeInTheDocument()
    expect(reads).toBe(2)
  })

  it('forgets a refused tax save once another request is picked', async () => {
    routes({
      'PUT /api/hub/freelancers/f1/fiscal': () =>
        answer(422, { detail: [{ loc: ['body', 'partita_iva'], msg: 'la partita IVA ha 11 cifre' }] }),
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    await userEvent.type(screen.getByLabelText('Partita IVA'), '9')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('la partita IVA ha 11 cifre')
    expect(screen.getByLabelText('Partita IVA')).toHaveAttribute('aria-invalid', 'true')

    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await pick(/Verdi Snc/)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('applies a tax save that lands after another request was picked, and leaves the step as it is', async () => {
    const late = deferred<Response>()
    const spy = routes({ 'PUT /api/hub/freelancers/f1/fiscal': () => late.promise })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 2, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await pick(/Verdi Snc/)
    // Opened again for the new request, before the save lands.
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Roma 1, Milano')
    // The server's own spelling of what was saved, so the test sees whose values show.
    late.resolve(answer(200, { ...FISCALE, domicilio: 'Via Po 2, 10121 Torino' }))

    await waitFor(() => expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Po 2, 10121 Torino'))
    expect(screen.getByRole('button', { name: 'Avanti' })).toBeEnabled()
    current('1. Chi e per chi')
    expect(screen.getByText('Verdi Snc · P.IVA 11122233344 · Torino')).toBeInTheDocument()
    expect(screen.queryByLabelText('Ruolo')).toBeNull()
    // Saved already, and shown as saved: «Avanti» does not write it again.
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toHaveLength(1)
  })

  it('keeps a tax field typed in while its save is on its way, and stays to show it', async () => {
    const late = deferred<Response>()
    let puts = 0
    const spy = routes({
      'PUT /api/hub/freelancers/f1/fiscal': (init?: RequestInit) =>
        ++puts === 1 ? late.promise : answer(200, { ...FISCALE, ...JSON.parse(String(init!.body)) }),
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 2, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 3, Torino')
    late.resolve(answer(200, { ...FISCALE, domicilio: 'Via Po 2, 10121 Torino' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Avanti' })).toBeEnabled())
    current('1. Chi e per chi')
    expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Po 3, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal').map((body) => body.domicilio)).toEqual([
      'Via Po 2, Torino',
      'Via Po 3, Torino',
    ])
  })

  it('keeps a completed tax save when the new request’s prefill read the tax data before it', async () => {
    const save = deferred<Response>()
    const verdi = deferred<Response>()
    const spy = routes({
      'PUT /api/hub/freelancers/f1/fiscal': () => save.promise,
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c9': () => verdi.promise,
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 2, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await userEvent.click(await screen.findByRole('button', { name: /Verdi Snc/ }))
    save.resolve(
      answer(200, {
        ...FISCALE,
        partita_iva: '01234567899',
        domicilio: 'Via Po 2, 10121 Torino',
        updated_at: '2026-09-25T10:00:00Z',
      }),
    )
    await waitFor(() => expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toHaveLength(1))
    // Verdi's prefill read the tax data before the save committed, and lands after it.
    verdi.resolve(
      answer(200, prefill({ cliente: { cliente_ragione_sociale: 'Verdi Snc', cliente_piva: '11122233344', cliente_sede: 'Torino' } })),
    )

    expect(await screen.findByText('Verdi Snc · P.IVA 11122233344 · Torino')).toBeInTheDocument()
    expect(screen.getByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567899')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Po 2, 10121 Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toHaveLength(1)
  })

  it('keeps a newer tax record the new request’s prefill brought when an older save lands after it', async () => {
    const save = deferred<Response>()
    const spy = routes({
      'PUT /api/hub/freelancers/f1/fiscal': () => save.promise,
      // Read after somebody else saved the tax data again, later than this page's save.
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c9': prefill({
        cliente: { cliente_ragione_sociale: 'Verdi Snc', cliente_piva: '11122233344', cliente_sede: 'Torino' },
        fiscale: { ...FISCALE, partita_iva: '05555555555', updated_at: '2026-09-25T12:00:00Z' },
      }),
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 2, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await pick(/Verdi Snc/)
    expect(screen.getByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 05555555555')).toBeInTheDocument()
    save.resolve(
      answer(200, { ...FISCALE, partita_iva: '01234567899', domicilio: 'Via Po 2, Torino', updated_at: '2026-09-25T10:00:00Z' }),
    )

    await waitFor(() => expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toHaveLength(1))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Avanti' })).toBeEnabled())
    expect(screen.getByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 05555555555')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('05555555555')
  })

  it('shows the newer record in a field an older tax save answered for, and keeps one typed again', async () => {
    const save = deferred<Response>()
    const spy = routes({
      'PUT /api/hub/freelancers/f1/fiscal': () => save.promise,
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c9': prefill({
        cliente: { cliente_ragione_sociale: 'Verdi Snc', cliente_piva: '11122233344', cliente_sede: 'Torino' },
        fiscale: { ...FISCALE, partita_iva: '05555555555', domicilio: 'Via Verdi 5, Torino', updated_at: '2026-09-25T12:00:00Z' },
      }),
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    await userEvent.clear(screen.getByLabelText('Domicilio professionale'))
    await userEvent.type(screen.getByLabelText('Domicilio professionale'), 'Via Po 2, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await pick(/Verdi Snc/)
    // For the new request: the domicilio as the save on its way sent it, and a PEC.
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 2, Torino')
    await userEvent.type(screen.getByLabelText('PEC, se ce l’ha'), 'ada@pec.it')
    save.resolve(answer(200, { ...FISCALE, domicilio: 'Via Po 2, Torino', updated_at: '2026-09-25T10:00:00Z' }))

    await waitFor(() => expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')).toHaveLength(1))
    await waitFor(() => expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Verdi 5, Torino'))
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('05555555555')
    expect(screen.getByLabelText('PEC, se ce l’ha')).toHaveValue('ada@pec.it')
  })

  /** Picks Rossi, sends an edited tax save that the test holds back, and moves to Verdi.
   *  Any later save is answered at once with what it sent. */
  async function saveHeldThenSwitch(late: ReturnType<typeof deferred<Response>>) {
    let puts = 0
    const spy = routes({
      'PUT /api/hub/freelancers/f1/fiscal': (init?: RequestInit) =>
        ++puts === 1 ? late.promise : answer(200, { ...FISCALE, ...JSON.parse(String(init!.body)) }),
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Po 2, Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await pick(/Verdi Snc/)
    return spy
  }

  it('shows a late tax save on the saved line of the new request', async () => {
    const late = deferred<Response>()
    await saveHeldThenSwitch(late)
    expect(screen.getByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')).toBeInTheDocument()
    late.resolve(answer(200, { ...FISCALE, partita_iva: '01234567899', domicilio: 'Via Po 2, 10121 Torino' }))
    expect(await screen.findByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567899')).toBeInTheDocument()
    current('1. Chi e per chi')
  })

  it('keeps what the admin typed for the new request when a late tax save lands', async () => {
    const late = deferred<Response>()
    const spy = await saveHeldThenSwitch(late)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    const domicilio = screen.getByLabelText('Domicilio professionale')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Via Nuova 3, Bari')
    late.resolve(answer(200, { ...FISCALE, partita_iva: '01234567899', domicilio: 'Via Po 2, 10121 Torino' }))

    // The field typed in keeps its text; the one left alone takes the saved value.
    await waitFor(() => expect(screen.getByLabelText('Partita IVA')).toHaveValue('01234567899'))
    expect(screen.getByLabelText('Domicilio professionale')).toHaveValue('Via Nuova 3, Bari')
    current('1. Chi e per chi')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')[1]).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567899',
      domicilio: 'Via Nuova 3, Bari',
      pec: null,
    })
  })
})

describe('step 2, «Condizioni»', () => {
  it('asks the engagement’s own conditions in order, prefilled', async () => {
    routes()
    mount()
    await toCondizioni()
    const form = screen.getByLabelText('Ruolo').closest('form')!
    const labels = [
      'Ruolo',
      'Cosa farà',
      'Inizio',
      'Fine prevista (facoltativa)',
      'Impegno',
      'Dove',
      'A giornata',
      'A corpo',
      'Compenso, IVA esclusa (€)',
    ]
    const fields = labels.map((label) => within(form).getByLabelText(label))
    fields.slice(1).forEach((after, index) => {
      expect(fields[index]!.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })
    expect(screen.getByLabelText('Cosa farà')).toHaveValue('Piattaforma di prenotazione')
    expect(screen.getByLabelText('Cosa farà').tagName).toBe('TEXTAREA')
    expect(screen.getByLabelText('Inizio')).toHaveValue('2026-10-01')
    expect(screen.getByLabelText('Impegno')).toHaveValue('3 mesi')
    expect(screen.getByLabelText('Dove')).toHaveValue('da remoto')
    expect(screen.getByRole('radio', { name: 'A giornata' })).toBeChecked()
    expect(screen.getByText('al giorno')).toBeInTheDocument()
    expect(screen.getByLabelText(/^Pagamento a/)).toHaveValue(30)
    expect(screen.getByRole('checkbox', { name: 'fine mese' })).toBeChecked()
    for (const label of ['Ruolo', 'Cosa farà', 'Inizio', 'Compenso, IVA esclusa (€)']) {
      expect(screen.getByLabelText(label)).toBeRequired()
    }
    expect(screen.getByLabelText(/^Pagamento a/)).toBeRequired()
    expect(screen.getByLabelText('Fine prevista (facoltativa)')).not.toBeRequired()
  })

  it('keeps «Altre condizioni» closed and opens it on a refusal naming one of its fields', async () => {
    routes({
      'POST /api/hub/freelancers/f1/matches/check': () =>
        answer(422, { detail: [{ loc: ['body', 'lettera', 'giorni_preavviso'], msg: 'Input should be greater than 0' }] }),
    })
    mount()
    await toCondizioni()
    const altre = screen.getByText('Altre condizioni (facoltative)').closest('details')!
    expect(altre).not.toHaveAttribute('open')
    expect(screen.getByLabelText('Referente del cliente')).toHaveValue('Mario Rossi')
    expect(screen.getByLabelText('Referente del cliente')).not.toBeVisible()
    expect(screen.getByLabelText('Giorni di preavviso')).not.toBeVisible()
    expect(screen.getByLabelText('Giorni di preavviso')).not.toHaveAttribute('min')
    for (const label of ['Spese', 'Esclusiva verso il cliente', 'Rapporti precedenti con il cliente']) {
      expect(within(altre).getByLabelText(label)).not.toBeRequired()
    }

    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Input should be greater than 0')
    await waitFor(() => expect(altre).toHaveAttribute('open'))
    expect(screen.getByLabelText('Giorni di preavviso')).toBeVisible()
    expect(screen.getByLabelText('Giorni di preavviso')).toHaveAttribute('aria-invalid', 'true')
    current('2. Condizioni')
  })

  it('«A corpo» adds its three fields after the fee and sets both modalità and unità', async () => {
    const spy = routes()
    mount()
    await toCondizioni()
    expect(screen.queryByLabelText('Risultati da consegnare')).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: 'A corpo' }))
    expect(screen.getByRole('radio', { name: 'A corpo' })).toBeChecked()
    expect(screen.getByText('in tutto')).toBeInTheDocument()
    const fee = screen.getByLabelText('Compenso, IVA esclusa (€)')
    expect(fee).toHaveValue('')
    await userEvent.type(fee, '12000')
    const added = ['Risultati da consegnare', 'Come il cliente accetta i risultati', 'Scadenze di fatturazione'].map(
      (label) => screen.getByLabelText(label),
    )
    for (const field of added) {
      expect(fee.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(field.compareDocumentPosition(screen.getByLabelText(/^Pagamento a/)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    await userEvent.type(added[0]!, 'Il modulo di prenotazione')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(body.lettera).toMatchObject({
      modalita: 'a corpo',
      unita: 'a corpo',
      compenso: '12000',
      risultati: 'Il modulo di prenotazione',
    })
  })

  it('leaves out what «A corpo» asked once the admin is back on «A giornata»', async () => {
    const spy = routes()
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('radio', { name: 'A corpo' }))
    await userEvent.type(screen.getByLabelText('Risultati da consegnare'), 'Il modulo')
    await userEvent.click(screen.getByRole('radio', { name: 'A giornata' }))
    expect(screen.queryByLabelText('Risultati da consegnare')).toBeNull()
    expect(screen.getByText('al giorno')).toBeInTheDocument()
    expect(screen.getByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('450')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(body.lettera).toMatchObject({ modalita: 'a giornata', unita: 'a giornata', compenso: '450', risultati: null })
  })

  it('keeps a fee the admin typed when switching between «A giornata» and «A corpo»', async () => {
    routes()
    mount()
    await toCondizioni()
    const fee = screen.getByLabelText('Compenso, IVA esclusa (€)')
    await userEvent.clear(fee)
    await userEvent.type(fee, '500')
    await userEvent.click(screen.getByRole('radio', { name: 'A corpo' }))
    expect(fee).toHaveValue('500')
    await userEvent.click(screen.getByRole('radio', { name: 'A giornata' }))
    expect(fee).toHaveValue('500')
  })

  it('shows the fee the way the letter writes it, with a decimal comma', async () => {
    const spy = routes({
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill({ compenso: '480.50' }),
    })
    mount()
    await toCondizioni()
    expect(screen.getByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('480,50')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(body.lettera.compenso).toBe('480.50')
  })

  it('shows an empty, required fee for a card without a day rate and names the fee the server refused', async () => {
    routes({
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill({ compenso: null }),
      'POST /api/hub/freelancers/f1/matches/check': () =>
        answer(422, { detail: [{ loc: ['body', 'lettera', 'compenso'], msg: 'Input should be greater than or equal to 1' }] }),
    })
    mount()
    await toCondizioni()
    const fee = screen.getByLabelText('Compenso, IVA esclusa (€)')
    expect(fee).toHaveValue('')
    expect(fee).toBeRequired()
    await userEvent.type(fee, '0')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Input should be greater than or equal to 1')
    expect(screen.getByLabelText('Compenso, IVA esclusa (€)')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.queryByRole('button', { name: 'Salva senza inviare' })).toBeNull()
  })

  it('names the end date the server refused for ending before the start (REB-412)', async () => {
    routes({
      'POST /api/hub/freelancers/f1/matches/check': () =>
        answer(422, { detail: [{ loc: ['body', 'lettera', 'data_fine'], msg: "la fine prevista viene prima dell'inizio" }] }),
    })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("la fine prevista viene prima dell'inizio")
    expect(screen.getByLabelText('Fine prevista (facoltativa)')).toHaveAttribute('aria-invalid', 'true')
  })

  it('names the payment term the server refused past 30 days from month end (REB-412)', async () => {
    routes({
      'POST /api/hub/freelancers/f1/matches/check': () =>
        answer(422, {
          detail: [
            {
              loc: ['body', 'lettera', 'giorni_pagamento'],
              msg: 'contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)',
            },
          ],
        }),
    })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)',
    )
    expect(screen.getByLabelText(/^Pagamento a/)).toHaveAttribute('aria-invalid', 'true')
  })

  it('drops a check that lands after the admin changed the conditions (Greptile 4092036036)', async () => {
    const late = deferred<Response>()
    let checks = 0
    routes({
      'POST /api/hub/freelancers/f1/matches/check': () => (++checks === 1 ? late.promise : CHECK_NO_QUADRO),
    })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.type(screen.getByLabelText('Ruolo'), ' senior')
    late.resolve(answer(200, CHECK_NO_QUADRO))

    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:anteprima-1'))
    current('2. Condizioni')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Avanti' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('link', { name: 'Apri la lettera (PDF)' })).toHaveAttribute('href', 'blob:anteprima-2')
  })

  it('asks for the check and the letter’s preview together', async () => {
    const held = deferred<Response>()
    const spy = routes({ 'POST /api/hub/freelancers/f1/matches/check': () => held.promise })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await waitFor(() =>
      expect(bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/preview?documento=lettera')).toHaveLength(1),
    )
    held.resolve(answer(200, CHECK))
    expect(await screen.findByRole('link', { name: 'Apri il contratto quadro (PDF)' })).toBeInTheDocument()
  })

  it('shows a preview that could not be written, and keeps no blob of it', async () => {
    routes({
      'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': () =>
        answer(503, { detail: 'Non riesco a scrivere il PDF adesso.' }),
    })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Non riesco a scrivere il PDF adesso.')
    current('2. Condizioni')
    const made = vi.mocked(URL.createObjectURL).mock.results.map((result) => result.value as string)
    for (const url of made) expect(URL.revokeObjectURL).toHaveBeenCalledWith(url)
  })

  it('takes the admin back to «Chi e per chi» to fix a client detail the check refused', async () => {
    let checks = 0
    const spy = routes({
      'POST /api/hub/freelancers/f1/matches/check': () =>
        ++checks === 1
          ? answer(422, { detail: [{ loc: ['body', 'cliente', 'cliente_piva'], msg: 'la partita IVA del cliente ha 11 cifre' }] })
          : CHECK,
    })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('la partita IVA del cliente ha 11 cifre')
    current('1. Chi e per chi')
    const piva = screen.getByLabelText('Partita IVA del cliente')
    expect(piva).toHaveAttribute('aria-invalid', 'true')
    await userEvent.clear(piva)
    await userEvent.type(piva, '01234567890')

    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(screen.queryByRole('alert')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    expect(bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')[1].cliente.cliente_piva).toBe('01234567890')
  })

  it('takes the admin back to the list when the check finds the request closed meanwhile', async () => {
    let checks = 0
    const spy = routes({
      'POST /api/hub/freelancers/f1/matches/check': () =>
        ++checks === 1
          ? answer(422, { detail: [{ loc: ['body', 'company_id'], msg: 'La richiesta è chiusa: scegline un’altra.' }] })
          : CHECK,
    })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('La richiesta è chiusa: scegline un’altra.')
    current('1. Chi e per chi')
    expect(screen.getByLabelText('Cerca una richiesta')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /Verdi Snc/ })).toBeEnabled()
    expect(screen.queryByRole('heading', { name: 'Cliente sulla lettera' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Avanti' })).toBeDisabled()

    await pick(/Verdi Snc/)
    expect(screen.queryByRole('alert')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    expect(bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')[1].company_id).toBe('c9')
  })

  it('forgets a refusal once the admin goes back from «Condizioni»', async () => {
    routes({
      'POST /api/hub/freelancers/f1/matches/check': () =>
        answer(422, { detail: [{ loc: ['body', 'lettera', 'ruolo'], msg: 'Il ruolo è troppo lungo' }] }),
    })
    mount()
    await toCondizioni()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Il ruolo è troppo lungo')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('step 3, «Controlla e invia»', () => {
  it('says what is about to happen and links both previews', async () => {
    const spy = routes()
    mount()
    await toControlla()
    for (const sentence of CHECK.riepilogo) expect(screen.getByText(sentence)).toBeInTheDocument()
    expect(screen.getByText(CHECK.cosa_succede)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri la lettera (PDF)' })).toHaveAttribute('href', 'blob:anteprima-1')
    expect(screen.getByRole('link', { name: 'Apri il contratto quadro (PDF)' })).toHaveAttribute('href', 'blob:anteprima-2')
    expect(screen.getByRole('button', { name: 'Indietro' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Invia ad Ada per la firma' })).toBeEnabled()
    // The check and the previews were asked about the same match.
    const [checked] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    const [previewed] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/preview?documento=lettera')
    expect(previewed).toEqual(checked)
  })

  it('goes back to «Condizioni» and forgets the stale preview, with no framework agreement to preview', async () => {
    routes({ 'POST /api/hub/freelancers/f1/matches/check': CHECK_NO_QUADRO })
    mount()
    await toControlla()
    expect(screen.getByText(CHECK_NO_QUADRO.cosa_succede)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Apri il contratto quadro (PDF)' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(await screen.findByLabelText('Ruolo')).toBeInTheDocument()
    current('2. Condizioni')
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:anteprima-1'))
  })

  it('sends and lands on «Match e contratti» with the report’s sentence', async () => {
    const spy = routes({
      'POST /api/hub/matches/m1/send': {
        match: { id: 'm1', lettera: { numero: '2026-001' } },
        inviato: 'quadro',
        mail_inviata: true,
      },
    })
    mount()
    await toControlla()
    await userEvent.click(screen.getByRole('button', { name: 'Invia ad Ada per la firma' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    expect(screen.getByTestId('notice')).toHaveTextContent(
      'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.',
    )
    expect(bodies(spy, 'POST', '/api/hub/freelancers/f1/matches')).toHaveLength(1)
  })

  it('stays on the last step with the sentence when the signing mail did not leave (REB-406)', async () => {
    const spy = routes({
      'POST /api/hub/matches/m1/send': {
        match: { id: 'm1', lettera: { numero: '2026-001' } },
        inviato: 'quadro',
        mail_inviata: false,
      },
    })
    mount()
    await toControlla()
    await userEvent.click(screen.getByRole('button', { name: 'Invia ad Ada per la firma' }))

    expect(
      await screen.findByText(
        'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma. La mail però non è partita: usa «Reinvia email».',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText('pagina contratti')).toBeNull()
    current('3. Controlla e invia')
    const link = screen.getByRole('link', { name: 'Vai a Match e contratti' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/contracts$/)
    // The match has left: it is no draft to save, and a second click must not send it again.
    expect(screen.getByRole('button', { name: 'Invia ad Ada per la firma' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Salva senza inviare' })).toBeDisabled()
    expect(spy.mock.calls.filter(([url]) => url === '/api/hub/matches/m1/send')).toHaveLength(1)
  })

  it('writes the match and sends it in one click, and after a refusal sends that same match again', async () => {
    let tries = 0
    const spy = routes({
      'POST /api/hub/matches/m1/send': () =>
        ++tries === 1
          ? answer(503, { detail: 'La firma elettronica non è attiva su questo ambiente.' })
          : { match: { id: 'm1', lettera: { numero: '2026-001' } }, inviato: 'quadro', mail_inviata: true },
    })
    mount()
    await toControlla()
    await userEvent.click(screen.getByRole('button', { name: 'Invia ad Ada per la firma' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('non è attiva')
    expect(alert).toHaveTextContent('La bozza è salvata')
    expect(screen.getByRole('button', { name: 'Indietro' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Invia ad Ada per la firma' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    expect(bodies(spy, 'POST', '/api/hub/freelancers/f1/matches')).toHaveLength(1)
    expect(tries).toBe(2)
  })

  it('sends the same match id again when the create response is lost on a retry (REB-406)', async () => {
    let creates = 0
    const spy = routes({
      'POST /api/hub/freelancers/f1/matches': () => {
        creates += 1
        // The first attempt's response never arrives (a network drop, not a status
        // code): the mutation's promise rejects exactly as a real `fetch` would.
        if (creates === 1) throw new Error('rete assente')
        return { id: 'm1' }
      },
    })
    mount()
    await toControlla()
    await userEvent.click(screen.getByRole('button', { name: 'Salva senza inviare' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva senza inviare' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()

    const [first, second] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches')
    expect(first.id).toBeTruthy()
    expect(second.id).toBe(first.id)
  })

  it('shows the server’s conflict with a link to «Match e contratti» and sends nothing (REB-406)', async () => {
    const spy = routes({
      'POST /api/hub/freelancers/f1/matches': () =>
        answer(409, {
          detail:
            'Questo match è già stato salvato con dati diversi: aprilo da «Match e contratti» e controllalo prima di inviarlo.',
        }),
    })
    mount()
    await toControlla()
    await userEvent.click(screen.getByRole('button', { name: 'Invia ad Ada per la firma' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Questo match è già stato salvato con dati diversi')
    const link = within(alert).getByRole('link', { name: 'Vai a Match e contratti' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/contracts$/)
    expect(screen.queryByText('pagina contratti')).toBeNull()
    expect(spy.mock.calls.some(([url]) => String(url).includes('/matches/m1/send'))).toBe(false)
  })
})

describe('«Giorni previsti», the expected days (REB-502)', () => {
  const HELP = 'Per il consuntivo: 8 ore al giorno. Il testo della lettera resta quello di «Impegno».'

  it('asks them on «Condizioni» after the fee, optional, a whole number, saying what they are for', async () => {
    routes()
    mount()
    await toCondizioni()
    const giorni = screen.getByLabelText('Giorni previsti')
    expect(giorni).toHaveAttribute('id', 'match-giorni_previsti')
    expect(giorni).toHaveAttribute('type', 'number')
    expect(giorni).toHaveAttribute('inputmode', 'numeric')
    expect(giorni).toHaveValue(null)
    expect(giorni).not.toBeRequired()
    // The column's own bounds; the default step of one refuses a fraction.
    expect(giorni).toHaveAttribute('min', '1')
    expect(giorni).toHaveAttribute('max', '366')
    expect(giorni).not.toHaveAttribute('step')
    expect(giorni).toHaveAccessibleDescription(HELP)
    const fee = screen.getByLabelText('Compenso, IVA esclusa (€)')
    expect(fee.compareDocumentPosition(giorni) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // In the open, not among the letter's own optional fields.
    const altre = screen.getByText('Altre condizioni (facoltative)').closest('details')!
    expect(altre).not.toContainElement(giorni)
  })

  it('sends them beside the letter, never inside it, and lists them on «Controlla e invia»', async () => {
    const spy = routes()
    mount()
    await toCondizioni()
    await userEvent.type(screen.getByLabelText('Giorni previsti'), '40')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByText('Giorni previsti: 40')).toBeInTheDocument()
    const [checked] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(checked.giorni_previsti).toBe(40)
    expect(checked.lettera).not.toHaveProperty('giorni_previsti')

    await userEvent.click(screen.getByRole('button', { name: 'Salva senza inviare' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches')
    expect(body.giorni_previsti).toBe(40)
    expect(body.lettera).not.toHaveProperty('giorni_previsti')
  })

  it('sends null for an empty box, and «Controlla e invia» lists nothing for it', async () => {
    const spy = routes()
    mount()
    await toControlla()
    expect(screen.queryByText(/Giorni previsti/)).toBeNull()
    const [checked] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(checked).toHaveProperty('giorni_previsti', null)
    expect(checked.lettera).not.toHaveProperty('giorni_previsti')
  })

  it.each(['0', '400', '12.5'])('leaves %s to the browser, which refuses it before any request', async (typed) => {
    const spy = routes()
    mount()
    await toCondizioni()
    await userEvent.type(screen.getByLabelText('Giorni previsti'), typed)
    expect(screen.getByLabelText('Giorni previsti')).toBeInvalid()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')).toHaveLength(0)
    current('2. Condizioni')
  })

  it('names the expected days the server refused, in its sentence, and stays on «Condizioni»', async () => {
    routes({
      'POST /api/hub/freelancers/f1/matches/check': () =>
        answer(422, { detail: [{ loc: ['body', 'giorni_previsti'], msg: 'I giorni previsti vanno da 1 a 366.' }] }),
    })
    mount()
    await toCondizioni()
    await userEvent.type(screen.getByLabelText('Giorni previsti'), '40')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('I giorni previsti vanno da 1 a 366.')
    expect(screen.getByLabelText('Giorni previsti')).toHaveAttribute('aria-invalid', 'true')
    current('2. Condizioni')
  })

  it('keeps them going back with the same request, and starts empty for another one', async () => {
    routes()
    mount()
    await toCondizioni()
    await userEvent.type(screen.getByLabelText('Giorni previsti'), '40')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Giorni previsti')).toHaveValue(40)

    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cambia richiesta' }))
    await userEvent.click(await screen.findByRole('button', { name: /Verdi Snc/ }))
    expect(await screen.findByText('Verdi Snc · P.IVA 11122233344 · Torino')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Giorni previsti')).toHaveValue(null)
  })
})
