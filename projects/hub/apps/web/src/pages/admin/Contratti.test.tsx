import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatDate } from '@/lib/format'
import { AdminContratti } from './Contratti'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** `lists.test.tsx`'s router of fetches, plus a handler that may be a function of the
 *  request, so one test can answer a GET differently after a POST. */
function routeFetch(handlers: Record<string, unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    if (!(key in handlers)) throw new Error(`unhandled fetch in this test: ${key}`)
    const handler = handlers[key]
    const body = typeof handler === 'function' ? (handler as (init?: RequestInit) => unknown)(init) : handler
    return body instanceof Response ? body : answer(200, body)
  })
}

const PERSON = { id: 'f1', nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it' }
const QUADRO = {
  id: 'd1',
  kind: 'quadro',
  freelancer_id: 'f1',
  match_id: null,
  numero: null,
  text_version: '0.1',
  testo_bozza: true,
  stato: 'firmato',
  created_at: '2026-09-23T10:00:00Z',
  created_by: 'a1',
  sent_at: '2026-09-23T11:00:00Z',
  signed_at: '2026-10-01T09:00:00Z',
  notice_at: null,
  ha_pdf_firmato: true,
  attivo: true,
  rinnovo: '2027-10-01',
  ultimo_giorno_disdetta: '2027-09-01',
  nuova_versione: true,
}
const LETTERA = {
  ...QUADRO,
  id: 'd2',
  kind: 'lettera',
  match_id: 'm1',
  numero: '2026-001',
  testo_bozza: true,
  stato: 'generato',
  sent_at: null,
  signed_at: null,
  ha_pdf_firmato: false,
  attivo: false,
  rinnovo: null,
  ultimo_giorno_disdetta: null,
  nuova_versione: false,
}
const MATCH = {
  id: 'm1',
  freelancer_id: 'f1',
  company_id: 'c1',
  nome_azienda: 'Rossi Studio',
  figura_richiesta: 'Backend developer',
  cliente_ragione_sociale: 'Rossi Studio S.r.l.',
  cliente_piva: '01234567890',
  cliente_sede: 'Milano',
  stato: 'bozza',
  created_at: '2026-09-23T10:00:00Z',
  created_by: 'a1',
  cancelled_at: null,
  updated_at: '2026-09-23T10:00:00Z',
  lettera: LETTERA,
}
const FISCALE = {
  freelancer_id: 'f1',
  codice_fiscale: 'LVLDAA85T50H501Z',
  partita_iva: '01234567890',
  domicilio: 'Via Roma 1, Milano',
  pec: null,
  updated_by: 'a1',
  updated_at: '2026-09-23T10:00:00Z',
}
const PAGE = { freelancer_id: 'f1', quadro: QUADRO, quadri: [QUADRO], matches: [MATCH], fiscale: FISCALE }

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const contratti = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: AdminContratti,
  })
  const card = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id',
    component: () => <p>scheda</p>,
  })
  const nuovoMatch = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/match/new',
    component: () => <p>nuovo match</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([contratti, card, nuovoMatch])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

afterEach(() => vi.restoreAllMocks())

describe('«Match e contratti» (REB-387)', () => {
  it('shows the framework agreement, its dates, its version and its PDFs', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    expect(await screen.findByRole('heading', { name: 'Match e contratti · Ada Lovelace' })).toBeInTheDocument()
    const section = screen.getByRole('region', { name: 'Contratto quadro' })
    expect(within(section).getByText('Firmato')).toBeInTheDocument()
    expect(within(section).getByText('Attivo')).toBeInTheDocument()
    expect(within(section).getByText(formatDate('2026-10-01T09:00:00Z'))).toBeInTheDocument()
    expect(within(section).getByText(formatDate('2027-10-01'))).toBeInTheDocument()
    expect(within(section).getByText(formatDate('2027-09-01'))).toBeInTheDocument()
    expect(within(section).getByText('Nuova versione disponibile')).toBeInTheDocument()
    expect(within(section).getByText('Testo in bozza')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'PDF del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf',
    )
    expect(within(section).getByRole('link', { name: 'PDF firmato del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf?firmato=true',
    )
    // A framework agreement never leaves on its own: it goes with a match, not with the
    // framework section, so «Invia per la firma» sits on the match's own row instead
    // (REB-406 fix round 1, M2: scoped to the row, so a button that landed in the
    // fiscal section or the header would still fail this).
    expect(within(section).queryByRole('button', { name: /Invia per la firma/ })).toBeNull()
    const row = screen.getByRole('row', { name: /Rossi Studio/ })
    expect(
      within(row).getByRole('button', { name: 'Invia per la firma il match con Rossi Studio' }),
    ).toBeInTheDocument()
  })

  it('lists the matches with their letter and cancels a draft after asking', async () => {
    let cancelled = false
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () =>
        cancelled
          ? { ...PAGE, matches: [{ ...MATCH, stato: 'annullato', lettera: { ...LETTERA, stato: 'annullato' } }] }
          : PAGE,
      'POST /api/hub/matches/m1/cancel': () => {
        cancelled = true
        return { ...MATCH, stato: 'annullato' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    const row = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(row).getByText('Bozza')).toBeInTheDocument()
    expect(within(row).getByText(/n\. 2026-001/)).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'PDF della lettera n. 2026-001' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d2/pdf',
    )
    await userEvent.click(within(row).getByRole('button', { name: 'Annulla il match con Rossi Studio' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla il match' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/cancel', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByText('Annullato')).toBeInTheDocument()
  })

  it('closes an active match', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [{ ...MATCH, stato: 'attivo' }] },
      'POST /api/hub/matches/m1/close': { ...MATCH, stato: 'concluso' },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Chiudi il match con Rossi Studio' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/close', expect.objectContaining({ method: 'POST' })))
  })

  it('saves the tax data from the page', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'PUT /api/hub/freelancers/f1/fiscal': { ...FISCALE, domicilio: 'Corso Como 1, Milano' },
    })
    mount('/admin/freelance/f1/contracts')
    const domicilio = await screen.findByLabelText('Domicilio professionale')
    expect(domicilio).toHaveValue('Via Roma 1, Milano')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Corso Como 1, Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(String(put[1]!.body))).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Corso Como 1, Milano',
      pec: null,
    })
  })

  it('says so, in words, when there is no framework agreement and no match yet', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { freelancer_id: 'f1', quadro: null, quadri: [], matches: [], fiscale: null },
    })
    mount('/admin/freelance/f1/contracts')
    expect(await screen.findByText('Nessun contratto quadro: lo genera il primo match.')).toBeInTheDocument()
    expect(screen.getByText('Nessun match per questa persona.')).toBeInTheDocument()
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('')
  })

  it('starts a new match from the page', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    const link = await screen.findByRole('link', { name: 'Crea match' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/match\/new$/)
  })

  it('sends a draft match for signature and says what left (REB-390)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/matches/m1/send': {
        match: { ...MATCH, stato: 'in_firma', lettera: { ...LETTERA, stato: 'in_attesa' } },
        inviato: 'quadro',
        mail_inviata: true,
      },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/send', expect.objectContaining({ method: 'POST' })))
    expect(
      await screen.findByText('Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.'),
    ).toBeInTheDocument()
  })

  it('offers no send for a letter that waits on a framework agreement already out for signature', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        quadro: { ...QUADRO, stato: 'inviato', attivo: false, signed_at: null },
        matches: [{ ...MATCH, stato: 'in_firma', lettera: { ...LETTERA, stato: 'in_attesa' } }],
      },
    })
    mount('/admin/freelance/f1/contracts')
    await screen.findByText('Rossi Studio')
    expect(screen.queryByRole('button', { name: /Invia per la firma/ })).toBeNull()
  })

  it('shows the server’s sentence when a text is still a draft', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/matches/m1/send': () =>
        answer(409, {
          detail:
            'Il testo della lettera di incarico è ancora una bozza (status: draft): si genera e si salva, ma non parte per la firma.',
        }),
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ancora una bozza')
  })

  const OUT = {
    ...QUADRO,
    stato: 'inviato',
    attivo: false,
    signed_at: null,
    ha_pdf_firmato: false,
    rinnovo: null,
    ultimo_giorno_disdetta: null,
    cancel_reason: null,
  }

  it('resends the signing mail and refreshes a framework agreement out for signature (REB-407)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro: OUT, quadri: [OUT] },
      'POST /api/hub/contract-documents/d1/resend': OUT,
      'POST /api/hub/contract-documents/d1/refresh': OUT,
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Reinvia email del contratto quadro' }))
    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Aggiorna stato del contratto quadro' }))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/resend', expect.objectContaining({ method: 'POST' }))
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/refresh', expect.objectContaining({ method: 'POST' }))
  })

  it('cancels a framework agreement out for signature after asking, and says why it is cancelled', async () => {
    let cancelled = false
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () => {
        const quadro = cancelled ? { ...OUT, stato: 'annullato', cancel_reason: 'Annullato da rebase.' } : OUT
        return { ...PAGE, quadro, quadri: [quadro] }
      },
      'POST /api/hub/contract-documents/d1/cancel': () => {
        cancelled = true
        return { ...OUT, stato: 'annullato', cancel_reason: 'Annullato da rebase.' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla il contratto quadro' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Sì, annulla il contratto quadro' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.objectContaining({ method: 'POST' })),
    )
    expect(await screen.findByText('Annullato da rebase.')).toBeInTheDocument()
  })

  it('records a notice on an active framework agreement after asking', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/contract-documents/d1/notice': { ...QUADRO, stato: 'disdetto', attivo: false },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Registra disdetta' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Sì, registra la disdetta' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/notice', expect.objectContaining({ method: 'POST' })),
    )
    expect(await screen.findByText('Disdetta registrata.')).toBeInTheDocument()
  })

  it('shows why a letter was cancelled and offers no signing action on it', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        matches: [
          {
            ...MATCH,
            stato: 'in_firma',
            lettera: { ...LETTERA, stato: 'annullato', cancel_reason: 'Rifiutato dal freelance: il compenso è sbagliato' },
          },
        ],
      },
    })
    mount('/admin/freelance/f1/contracts')
    const row = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(row).getByText('Rifiutato dal freelance: il compenso è sbagliato')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Reinvia|Aggiorna stato|Invia per la firma/ })).toBeNull()
    expect(within(row).getByRole('button', { name: 'Annulla il match con Rossi Studio' })).toBeInTheDocument()
  })

  it('warns that cancelling a match whose letter left stops the freelancer’s link', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        matches: [{ ...MATCH, stato: 'in_firma', lettera: { ...LETTERA, stato: 'inviato' } }],
      },
    })
    mount('/admin/freelance/f1/contracts')
    const row = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(row).getByRole('button', { name: 'Reinvia email della lettera n. 2026-001' })).toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: 'Annulla il match con Rossi Studio' }))
    expect(await screen.findByText(/il link ricevuto dal freelance smette di funzionare/)).toBeInTheDocument()
  })

  it('shows when a document out for signature left, so the admin knows when to check Documenso', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        quadro: OUT,
        quadri: [OUT],
        matches: [{ ...MATCH, stato: 'in_firma', lettera: { ...LETTERA, stato: 'inviato', sent_at: '2026-09-24T08:00:00Z' } }],
      },
    })
    mount('/admin/freelance/f1/contracts')
    const section = await screen.findByRole('region', { name: 'Contratto quadro' })
    expect(within(section).getByText(`Inviato il ${formatDate(OUT.sent_at)}`)).toBeInTheDocument()
    const row = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(row).getByText(`Inviato il ${formatDate('2026-09-24T08:00:00Z')}`)).toBeInTheDocument()
  })
})
