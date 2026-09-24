import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor } from '@testing-library/react'
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
const FISCALE = {
  freelancer_id: 'f1',
  codice_fiscale: 'LVLDAA85T50H501Z',
  partita_iva: '01234567890',
  domicilio: 'Via Roma 1, Milano',
  pec: null,
  updated_by: 'a1',
  updated_at: '2026-09-23T10:00:00Z',
}
function prefill(compenso: string | null) {
  return {
    fiscale: FISCALE,
    cliente: { cliente_ragione_sociale: 'Rossi Studio', cliente_piva: null, cliente_sede: null },
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
    component: () => <p>pagina contratti</p>,
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

async function throughTheFirstThreeSteps() {
  await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
  expect(await screen.findByLabelText('Codice fiscale')).toHaveValue('LVLDAA85T50H501Z')
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
  expect(await screen.findByLabelText('Ragione sociale del cliente')).toHaveValue('Rossi Studio')
  await userEvent.type(screen.getByLabelText('Partita IVA del cliente'), '09876543210')
  await userEvent.type(screen.getByLabelText('Sede del cliente'), 'Milano')
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
}

describe('«Crea match» in five steps (REB-387)', () => {
  it('walks from a request to a draft, previews both documents and saves the draft', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 2, items: [OPEN, CLOSED], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
      'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': pdf,
      'POST /api/hub/freelancers/f1/matches': { id: 'm1' },
    })
    mount()
    expect(await screen.findByRole('button', { name: /Bianchi Srl/ })).toBeDisabled()
    expect(screen.getByText('1. Azienda')).toHaveAttribute('aria-current', 'step')
    await throughTheFirstThreeSteps()

    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(String(put[1]!.body))).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Via Roma 1, Milano',
      pec: null,
    })
    expect(await screen.findByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('450.00')
    expect(screen.getByLabelText('Ruolo')).toHaveValue('Backend developer')
    expect(document.body.textContent).not.toMatch(/777\.77/)

    await userEvent.click(screen.getByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByRole('link', { name: 'Apri la lettera di incarico' })).toHaveAttribute('href', 'blob:anteprima-1')
    expect(screen.getByRole('link', { name: 'Apri il contratto quadro' })).toHaveAttribute('href', 'blob:anteprima-2')
    expect(screen.getByText(/partirà per primo il contratto quadro/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Invia per la firma' })).toBeEnabled()
    expect(document.body.textContent).not.toMatch(/777\.77/)

    await userEvent.click(screen.getByRole('button', { name: 'Salva come bozza' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    const created = spy.mock.calls.find(([url, init]) => url === '/api/hub/freelancers/f1/matches' && init?.method === 'POST')!
    const body = JSON.parse(String(created[1]!.body))
    expect(body.company_id).toBe('c1')
    expect(body.cliente).toEqual({ cliente_ragione_sociale: 'Rossi Studio', cliente_piva: '09876543210', cliente_sede: 'Milano' })
    expect(body.lettera).toMatchObject({ ruolo: 'Backend developer', compenso: '450.00', giorni_pagamento: 30, fine_mese: true, risultati: null })
    expect(JSON.stringify(body)).not.toMatch(/budget|777\.77/)
  })

  it('shows an empty, required fee for a card without a day rate and names the fee the server refused', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill(null),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': () =>
        answer(422, { detail: [{ loc: ['body', 'lettera', 'compenso'], msg: 'Input should be greater than or equal to 1' }] }),
    })
    mount()
    await throughTheFirstThreeSteps()
    const fee = await screen.findByLabelText('Compenso, IVA esclusa (€)')
    expect(fee).toHaveValue('')
    expect(fee).toBeRequired()
    await userEvent.type(fee, '0')
    await userEvent.click(screen.getByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Input should be greater than or equal to 1')
    expect(screen.getByLabelText('Compenso, IVA esclusa (€)')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.queryByRole('button', { name: 'Salva come bozza' })).toBeNull()
  })

  it('names the end date the server refused for ending before the start (REB-412)', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': () =>
        answer(422, {
          detail: [{ loc: ['body', 'lettera', 'data_fine'], msg: "la fine prevista viene prima dell'inizio" }],
        }),
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(screen.getByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("la fine prevista viene prima dell'inizio")
    expect(screen.getByLabelText('Fine prevista')).toHaveAttribute('aria-invalid', 'true')
  })

  it('names the payment term the server refused past 30 days from month end (REB-412)', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': () =>
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
    await throughTheFirstThreeSteps()
    await userEvent.click(screen.getByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)',
    )
    expect(screen.getByLabelText('Giorni di pagamento')).toHaveAttribute('aria-invalid', 'true')
  })

  it('goes back from the preview to the letter and forgets the stale preview', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': { ...prefill('450.00'), quadro_necessario: false, lettera_in_attesa: false },
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByText(/partirà solo la lettera di incarico/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Apri il contratto quadro' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(await screen.findByLabelText('Ruolo')).toBeInTheDocument()
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:anteprima-1'))
  })

  it('keeps an edited letter after going back to Azienda and forward with the same company (REB-402)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
    })
    mount()
    await throughTheFirstThreeSteps()
    const ruolo = await screen.findByLabelText('Ruolo')
    await userEvent.clear(ruolo)
    await userEvent.type(ruolo, 'Ruolo modificato')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // Lettera -> Cliente
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // Cliente -> Freelance
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // Freelance -> Azienda
    expect(screen.getByText('1. Azienda')).toHaveAttribute('aria-current', 'step')
    const prefillCallsBefore = spy.mock.calls.filter(([url]) => String(url).includes('/matches/prefill')).length
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Codice fiscale')).toHaveValue('LVLDAA85T50H501Z')
    const prefillCallsAfter = spy.mock.calls.filter(([url]) => String(url).includes('/matches/prefill')).length
    expect(prefillCallsAfter).toBe(prefillCallsBefore)
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Freelance -> Cliente
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Cliente -> Lettera
    expect(await screen.findByLabelText('Ruolo')).toHaveValue('Ruolo modificato')
  })

  it('reloads the prefill when the company changes after going back to Azienda (REB-402)', async () => {
    const OPEN2 = { ...OPEN, id: 'c9', nome_azienda: 'Verdi Snc', referente: 'Luca Verdi' }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 2, items: [OPEN, OPEN2], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c9': {
        ...prefill('900.00'),
        cliente: { cliente_ragione_sociale: 'Verdi Snc', cliente_piva: null, cliente_sede: null },
      },
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
    })
    mount()
    await throughTheFirstThreeSteps()
    expect(screen.getByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('450.00')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // Lettera -> Cliente
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // Cliente -> Freelance
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // Freelance -> Azienda
    await userEvent.click(await screen.findByRole('button', { name: /Verdi Snc/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Codice fiscale')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Freelance -> Cliente
    expect(await screen.findByLabelText('Ragione sociale del cliente')).toHaveValue('Verdi Snc')
  })

  it('drops a prefill that lands after the admin picked another company (Greptile 4092036036)', async () => {
    const OPEN2 = { ...OPEN, id: 'c9', nome_azienda: 'Verdi Snc', referente: 'Luca Verdi' }
    const late = deferred<Response>()
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 2, items: [OPEN, OPEN2], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': () => late.promise,
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c9': {
        ...prefill('900.00'),
        cliente: { cliente_ragione_sociale: 'Verdi Snc', cliente_piva: null, cliente_sede: null },
      },
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(screen.getByRole('button', { name: /Verdi Snc/ }))
    late.resolve(answer(200, prefill('450.00')))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Avanti' })).toBeEnabled())
    expect(screen.getByText('1. Azienda')).toHaveAttribute('aria-current', 'step')
    expect(screen.getByRole('button', { name: /Verdi Snc/ })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Codice fiscale')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Freelance -> Cliente
    expect(await screen.findByLabelText('Ragione sociale del cliente')).toHaveValue('Verdi Snc')
  })

  it('drops a preview that lands after the admin went back and changed the client (Greptile 4092036036)', async () => {
    const late = deferred<Response>()
    let letters = 0
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': { ...prefill('450.00'), quadro_necessario: false, lettera_in_attesa: false },
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': () => (++letters === 1 ? late.promise : pdf()),
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // Lettera -> Cliente
    const sede = await screen.findByLabelText('Sede del cliente')
    await userEvent.clear(sede)
    await userEvent.type(sede, 'Torino')
    late.resolve(pdf())

    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:anteprima-1'))
    expect(screen.getByText('3. Cliente')).toHaveAttribute('aria-current', 'step')
    expect(screen.queryByRole('button', { name: 'Salva come bozza' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Cliente -> Lettera
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByRole('link', { name: 'Apri la lettera di incarico' })).toHaveAttribute('href', 'blob:anteprima-2')
  })

  it('reaches a request beyond the first page through «Mostra altre» (Greptile 4092036042)', async () => {
    const OLDER = { ...OPEN, id: 'c7', nome_azienda: 'Neri Spa', referente: 'Anna Neri', created_at: '2026-01-10T10:00:00Z' }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 51, items: [OPEN], per_stato: {}, next_cursor: 'p2' },
      'GET /api/hub/companies?limit=50&cursor=p2': { totale: 51, items: [OLDER], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c7': {
        ...prefill('450.00'),
        cliente: { cliente_ragione_sociale: 'Neri Spa', cliente_piva: null, cliente_sede: null },
      },
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
    })
    mount()
    await screen.findByRole('button', { name: /Rossi Studio/ })
    expect(screen.queryByRole('button', { name: /Neri Spa/ })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Mostra altre' }))
    await userEvent.click(await screen.findByRole('button', { name: /Neri Spa/ }))
    expect(screen.getByRole('button', { name: /Rossi Studio/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mostra altre' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Codice fiscale')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Freelance -> Cliente
    expect(await screen.findByLabelText('Ragione sociale del cliente')).toHaveValue('Neri Spa')
  })

  it('writes the match and sends it in one click, and after a refusal sends that same match again', async () => {
    let tries = 0
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
      'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': pdf,
      'POST /api/hub/freelancers/f1/matches': { id: 'm1' },
      'POST /api/hub/matches/m1/send': () =>
        ++tries === 1
          ? answer(503, { detail: 'La firma elettronica non è attiva su questo ambiente.' })
          : { match: { id: 'm1' }, inviato: 'quadro', mail_inviata: true },
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('non è attiva')
    expect(alert).toHaveTextContent('La bozza è salvata')
    await userEvent.click(screen.getByRole('button', { name: 'Invia per la firma' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    const creates = spy.mock.calls.filter(
      ([url, init]) => url === '/api/hub/freelancers/f1/matches' && init?.method === 'POST',
    )
    expect(creates).toHaveLength(1)
    expect(tries).toBe(2)
  })

  it('sends the same match id again when the create response is lost on a retry (REB-406)', async () => {
    let creates = 0
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
      'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': pdf,
      'POST /api/hub/freelancers/f1/matches': () => {
        creates += 1
        // The first attempt's response never arrives (a network drop, not a status
        // code): the mutation's promise rejects exactly as a real `fetch` would.
        if (creates === 1) throw new Error('rete assente')
        return { id: 'm1' }
      },
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))

    await userEvent.click(await screen.findByRole('button', { name: 'Salva come bozza' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva come bozza' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()

    const creates_calls = spy.mock.calls.filter(
      ([url, init]) => url === '/api/hub/freelancers/f1/matches' && init?.method === 'POST',
    )
    expect(creates_calls).toHaveLength(2)
    const [first, second] = creates_calls.map(([, init]) => JSON.parse(String(init!.body)))
    expect(first.id).toBeTruthy()
    expect(second.id).toBe(first.id)
  })

  it('stays on the preview and points at «Match e contratti» when the signing mail did not leave (REB-406)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
      'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': pdf,
      'POST /api/hub/freelancers/f1/matches': { id: 'm1' },
      'POST /api/hub/matches/m1/send': {
        match: { id: 'm1', lettera: { numero: '2026-001' } },
        inviato: 'quadro',
        mail_inviata: false,
      },
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma' }))

    expect(
      await screen.findByText(
        'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma. La mail però non è partita: usa «Reinvia email».',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText('pagina contratti')).toBeNull()
    const link = screen.getByRole('link', { name: 'Vai a Match e contratti' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/contracts$/)

    // A report already showing means this send already happened once: the button stays
    // disabled so a second click cannot send it again.
    const sendButton = screen.getByRole('button', { name: 'Invia per la firma' })
    expect(sendButton).toBeDisabled()
    const sends = spy.mock.calls.filter(([url]) => url === '/api/hub/matches/m1/send')
    expect(sends).toHaveLength(1)
  })
})
