import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatDate } from '@/lib/format'
import { AdminContratti } from './Contratti'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** `lists.test.tsx`'s router of fetches, plus a handler that may be a function of the
 *  request, so one test can answer a GET differently after a POST, and may itself
 *  answer a promise the test resolves later (`deferred`), to catch a button mid-request. */
function routeFetch(handlers: Record<string, unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    if (!(key in handlers)) throw new Error(`unhandled fetch in this test: ${key}`)
    const handler = handlers[key]
    const body = await (typeof handler === 'function' ? (handler as (init?: RequestInit) => unknown)(init) : handler)
    return body instanceof Response ? body : answer(200, body)
  })
}

/** A response the test answers when it chooses, to catch a button mid-request. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

const PERSON = { id: 'f1', nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it' }
// Each fixture carries the sentence and the actions the core's `match_words` answers for
// its state: the page renders them and decides nothing of its own.
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
  cancel_reason: null,
  ha_pdf_firmato: true,
  attivo: true,
  rinnovo: '2027-10-01',
  ultimo_giorno_disdetta: '2027-09-01',
  nuova_versione: true,
  situazione:
    'Firmato il 1° ottobre 2026. Si rinnova da solo il 1° ottobre 2027; disdetta entro il 1° settembre 2027. Il testo è ancora in bozza.',
  prossima_azione: null,
  altre_azioni: ['aggiorna_stato', 'registra_disdetta'],
}
const LETTERA = {
  ...QUADRO,
  id: 'd2',
  kind: 'lettera',
  match_id: 'm1',
  numero: '2026-001',
  stato: 'generato',
  sent_at: null,
  signed_at: null,
  ha_pdf_firmato: false,
  attivo: false,
  rinnovo: null,
  ultimo_giorno_disdetta: null,
  nuova_versione: false,
  situazione: 'Pronta, non ancora inviata.',
  prossima_azione: null,
  altre_azioni: [],
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
  situazione: 'Da inviare: la lettera n. 2026-001 è pronta, il freelance non ha ancora ricevuto nulla.',
  prossima_azione: 'invia',
  altre_azioni: ['annulla'],
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
/** A framework agreement out for signature, waiting for the freelancer. */
const OUT = {
  ...QUADRO,
  stato: 'inviato',
  testo_bozza: false,
  attivo: false,
  signed_at: null,
  ha_pdf_firmato: false,
  rinnovo: null,
  ultimo_giorno_disdetta: null,
  situazione: 'Inviato il 23 settembre 2026: aspetta la firma del freelance.',
  prossima_azione: 'reinvia_email',
  altre_azioni: ['aggiorna_stato', 'annulla'],
}

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

const quadroCard = () => screen.findByRole('region', { name: 'Contratto quadro' })
const matchCard = () => screen.findByRole('article', { name: 'Rossi Studio · Backend developer' })
const buttonNames = (card: HTMLElement) => within(card).queryAllByRole('button').map((button) => button.getAttribute('aria-label') ?? button.textContent)

/** Opens a card's «Altre azioni» and answers the names of what it holds. */
async function openMore(card: HTMLElement, name: string) {
  await userEvent.click(within(card).getByRole('button', { name }))
  const items = await screen.findAllByRole('menuitem')
  return items.map((item) => item.getAttribute('aria-label'))
}

afterEach(() => vi.restoreAllMocks())

describe('«Match e contratti» as cards (REB-477)', () => {
  it('shows the framework agreement as a card: its state, its sentence, its PDFs, and the rest in a closed «Dettagli»', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    expect(await screen.findByRole('heading', { name: 'Match e contratti · Ada Lovelace' })).toBeInTheDocument()
    const card = await quadroCard()
    expect(within(card).getByText('Firmato')).toBeInTheDocument()
    expect(within(card).getByText(QUADRO.situazione)).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'PDF del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf',
    )
    expect(within(card).getByRole('link', { name: 'PDF firmato del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf?firmato=true',
    )
    const details = within(card).getByText('Dettagli').closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(within(details).getByText('0.1')).toBeInTheDocument()
    expect(within(details).getByText('Nuova versione disponibile')).toBeInTheDocument()
    // The dates and the draft text are in the sentence: no row or badge repeats them.
    expect(within(card).queryByText('Prossimo rinnovo')).toBeNull()
    expect(within(card).queryByText('Testo in bozza')).toBeNull()
    expect(within(card).queryByText(formatDate('2027-10-01'))).toBeNull()
  })

  it('says, when there is none, that the first match sent brings the framework agreement', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { freelancer_id: 'f1', quadro: null, quadri: [], matches: [], fiscale: null },
    })
    mount('/admin/freelance/f1/contracts')
    const card = await quadroCard()
    expect(within(card).getByText('Nessun contratto quadro: parte con il primo match inviato.')).toBeInTheDocument()
    expect(within(card).queryAllByRole('button')).toHaveLength(0)
    expect(screen.getByText('Nessun match per questa persona.')).toBeInTheDocument()
  })

  const FRAMEWORK_CASES = [
    {
      stato: 'generato',
      pill: 'Pronto, non inviato',
      situazione: 'Pronto, non ancora inviato: parte con «Invia per la firma» sul match.',
      prossima_azione: null,
      altre_azioni: ['annulla'],
      primary: [],
      more: ['Annulla il contratto quadro'],
    },
    {
      stato: 'inviato',
      pill: 'Da firmare',
      situazione: 'Inviato il 23 settembre 2026: aspetta la firma del freelance.',
      prossima_azione: 'reinvia_email',
      altre_azioni: ['aggiorna_stato', 'annulla'],
      primary: ['Reinvia email del contratto quadro'],
      more: ['Aggiorna stato del contratto quadro', 'Annulla il contratto quadro'],
    },
    {
      stato: 'firmato',
      pill: 'Firmato',
      situazione: 'Firmato il 1° ottobre 2026; la copia firmata non è ancora arrivata.',
      prossima_azione: 'aggiorna_stato',
      altre_azioni: ['registra_disdetta'],
      primary: ['Aggiorna stato del contratto quadro'],
      more: ['Registra disdetta del contratto quadro'],
    },
    {
      stato: 'annullato',
      pill: 'Annullato',
      situazione: 'Rifiutato dal freelance: il compenso è sbagliato.',
      prossima_azione: null,
      altre_azioni: [],
      primary: [],
      more: [],
    },
    {
      stato: 'disdetto',
      pill: 'Disdetto',
      situazione: 'Disdetto il 2 ottobre 2026.',
      prossima_azione: null,
      altre_azioni: [],
      primary: [],
      more: [],
    },
  ]

  it.each(FRAMEWORK_CASES)(
    'a framework agreement «$pill» reads its sentence and offers exactly the core’s next step',
    async ({ stato, pill, situazione, prossima_azione, altre_azioni, primary, more }) => {
      const quadro = { ...QUADRO, stato, situazione, prossima_azione, altre_azioni }
      routeFetch({
        'GET /api/hub/freelancers/f1': PERSON,
        'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro, quadri: [quadro] },
      })
      mount('/admin/freelance/f1/contracts')
      const card = await quadroCard()
      expect(within(card).getByText(pill)).toBeInTheDocument()
      expect(within(card).getByText(situazione)).toBeInTheDocument()
      const trigger = more.length ? ['Altre azioni del contratto quadro'] : []
      expect(buttonNames(card)).toEqual([...primary, ...trigger])
      if (more.length) expect(await openMore(card, 'Altre azioni del contratto quadro')).toEqual(more)
    },
  )

  const MATCH_CASES = [
    {
      name: 'a draft',
      stato: 'bozza',
      lettera: {},
      pill: 'Da inviare',
      situazione: MATCH.situazione,
      prossima_azione: 'invia',
      altre_azioni: ['annulla'],
      primary: ['Invia per la firma il match con Rossi Studio'],
      more: ['Annulla il match con Rossi Studio'],
    },
    {
      name: 'a letter waiting for a framework agreement out for signature',
      stato: 'in_firma',
      lettera: { stato: 'in_attesa' },
      pill: 'In attesa di firma',
      situazione: 'La lettera n. 2026-001 aspetta la firma del contratto quadro e parte da sola dopo.',
      prossima_azione: null,
      altre_azioni: ['annulla'],
      primary: [],
      more: ['Annulla il match con Rossi Studio'],
    },
    {
      name: 'a letter out for signature',
      stato: 'in_firma',
      lettera: { stato: 'inviato', sent_at: '2026-09-24T08:00:00Z' },
      pill: 'In attesa di firma',
      situazione: 'Lettera n. 2026-001 inviata il 24 settembre 2026: aspetta la firma del freelance.',
      prossima_azione: 'reinvia_email',
      altre_azioni: ['aggiorna_stato', 'annulla'],
      primary: ['Reinvia email della lettera n. 2026-001'],
      more: ['Aggiorna stato della lettera n. 2026-001', 'Annulla il match con Rossi Studio'],
    },
    {
      name: 'an active match',
      stato: 'attivo',
      lettera: { stato: 'firmato', ha_pdf_firmato: true },
      pill: 'Attivo',
      situazione: 'Attivo: lettera n. 2026-001 firmata il 1° ottobre 2026, dal 1° ottobre 2026.',
      prossima_azione: null,
      altre_azioni: ['chiudi'],
      primary: [],
      more: ['Chiudi il match con Rossi Studio'],
    },
    {
      name: 'a closed match',
      stato: 'concluso',
      lettera: { stato: 'firmato', ha_pdf_firmato: true },
      pill: 'Concluso',
      situazione: 'Concluso: lettera n. 2026-001, dal 1° ottobre 2026 al 31 dicembre 2026.',
      prossima_azione: null,
      altre_azioni: [],
      primary: [],
      more: [],
    },
    {
      name: 'a match cancelled after the freelancer refused',
      stato: 'annullato',
      lettera: { stato: 'annullato', cancel_reason: 'Rifiutato dal freelance: il compenso è sbagliato' },
      pill: 'Annullato',
      situazione:
        'Annullato: la lettera n. 2026-001 non va più firmata. Rifiutato dal freelance: il compenso è sbagliato.',
      prossima_azione: null,
      altre_azioni: [],
      primary: [],
      more: [],
    },
  ]

  it.each(MATCH_CASES)(
    '$name reads its sentence and offers exactly the core’s next step',
    async ({ stato, lettera, pill, situazione, prossima_azione, altre_azioni, primary, more }) => {
      const match = { ...MATCH, stato, lettera: { ...LETTERA, ...lettera }, situazione, prossima_azione, altre_azioni }
      routeFetch({
        'GET /api/hub/freelancers/f1': PERSON,
        'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [match] },
      })
      mount('/admin/freelance/f1/contracts')
      const card = await matchCard()
      expect(within(card).getByText(pill)).toBeInTheDocument()
      expect(within(card).getByText(situazione)).toBeInTheDocument()
      const trigger = more.length ? ['Altre azioni del match con Rossi Studio'] : []
      expect(buttonNames(card)).toEqual([...primary, ...trigger])
      if (more.length) expect(await openMore(card, 'Altre azioni del match con Rossi Studio')).toEqual(more)
    },
  )

  it('shows a match’s letter PDFs and when it was created, and never a send on the framework card', async () => {
    const signed = { ...LETTERA, stato: 'firmato', ha_pdf_firmato: true }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [{ ...MATCH, lettera: signed }] },
    })
    mount('/admin/freelance/f1/contracts')
    const card = await matchCard()
    expect(within(card).getByRole('link', { name: 'PDF della lettera n. 2026-001' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d2/pdf',
    )
    expect(within(card).getByRole('link', { name: 'PDF firmato della lettera n. 2026-001' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d2/pdf?firmato=true',
    )
    expect(within(card).getByText(`Creato il ${formatDate(MATCH.created_at)}`)).toBeInTheDocument()
    // A framework agreement leaves with a match, so «Invia per la firma» is the match's
    // alone (REB-406).
    expect(within(await quadroCard()).queryByRole('button', { name: /Invia per la firma/ })).toBeNull()
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
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.',
    )
  })

  it('cancels a match from «Altre azioni» only after asking', async () => {
    let cancelled = false
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () =>
        cancelled
          ? {
              ...PAGE,
              matches: [
                {
                  ...MATCH,
                  stato: 'annullato',
                  lettera: { ...LETTERA, stato: 'annullato' },
                  situazione: 'Annullato: la lettera n. 2026-001 non va più firmata.',
                  prossima_azione: null,
                  altre_azioni: [],
                },
              ],
            }
          : PAGE,
      'POST /api/hub/matches/m1/cancel': () => {
        cancelled = true
        return { ...MATCH, stato: 'annullato' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Annulla il match con Rossi Studio' }))
    expect(await screen.findByRole('dialog', { name: 'Annullare il match?' })).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalledWith('/api/hub/matches/m1/cancel', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Annulla il match' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/cancel', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByText('Annullato: la lettera n. 2026-001 non va più firmata.')).toBeInTheDocument()
    expect(within(await matchCard()).getByText('Annullato')).toBeInTheDocument()
  })

  it('gives the focus back to «Altre azioni» when the question is dismissed, since the item that asked is gone', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    const card = await quadroCard()
    await openMore(card, 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Registra disdetta del contratto quadro' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Indietro' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Altre azioni del contratto quadro' })).toHaveFocus(),
    )
  })

  it('warns that cancelling a match whose letter left stops the freelancer’s link', async () => {
    const match = MATCH_CASES[2]!
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        matches: [
          {
            ...MATCH,
            stato: match.stato,
            lettera: { ...LETTERA, ...match.lettera },
            situazione: match.situazione,
            prossima_azione: match.prossima_azione,
            altre_azioni: match.altre_azioni,
          },
        ],
      },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Annulla il match con Rossi Studio' }))
    expect(await screen.findByText(/il link ricevuto dal freelance smette di funzionare/)).toBeInTheDocument()
  })

  it('closes an active match from «Altre azioni»', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        matches: [{ ...MATCH, stato: 'attivo', prossima_azione: null, altre_azioni: ['chiudi'] }],
      },
      'POST /api/hub/matches/m1/close': { ...MATCH, stato: 'concluso' },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Chiudi il match con Rossi Studio' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/close', expect.objectContaining({ method: 'POST' })))
  })

  it('resends a letter’s mail and reads its state from its match’s card', async () => {
    const out = MATCH_CASES[2]!
    const match = {
      ...MATCH,
      stato: out.stato,
      lettera: { ...LETTERA, ...out.lettera },
      situazione: out.situazione,
      prossima_azione: out.prossima_azione,
      altre_azioni: out.altre_azioni,
    }
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [match] },
      'POST /api/hub/contract-documents/d2/resend': match.lettera,
      'POST /api/hub/contract-documents/d2/refresh': match.lettera,
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(within(await matchCard()).getByRole('button', { name: 'Reinvia email della lettera n. 2026-001' }))
    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Aggiorna stato della lettera n. 2026-001' }))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d2/resend', expect.objectContaining({ method: 'POST' }))
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d2/refresh', expect.objectContaining({ method: 'POST' }))
  })

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
    await openMore(await quadroCard(), 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Aggiorna stato del contratto quadro' }))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/resend', expect.objectContaining({ method: 'POST' }))
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/refresh', expect.objectContaining({ method: 'POST' }))
  })

  it('cancels a framework agreement out for signature only after asking, and reads the new sentence', async () => {
    let cancelled = false
    const gone = { ...OUT, stato: 'annullato', cancel_reason: 'Annullato da rebase.', situazione: 'Annullato da rebase.', prossima_azione: null, altre_azioni: [] }
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () => {
        const quadro = cancelled ? gone : OUT
        return { ...PAGE, quadro, quadri: [quadro] }
      },
      'POST /api/hub/contract-documents/d1/cancel': () => {
        cancelled = true
        return gone
      },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await quadroCard(), 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Annulla il contratto quadro' }))
    expect(await screen.findByRole('dialog', { name: 'Annullare il contratto quadro?' })).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Sì, annulla il contratto quadro' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.objectContaining({ method: 'POST' })),
    )
    expect(await screen.findByText('Annullato da rebase.')).toBeInTheDocument()
    expect(within(await quadroCard()).queryByRole('button', { name: /Altre azioni/ })).toBeNull()
  })

  it('records a notice on an active framework agreement only after asking', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/contract-documents/d1/notice': { ...QUADRO, stato: 'disdetto', attivo: false },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await quadroCard(), 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Registra disdetta del contratto quadro' }))
    expect(await screen.findByRole('dialog', { name: 'Registrare la disdetta?' })).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalledWith('/api/hub/contract-documents/d1/notice', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Sì, registra la disdetta' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/notice', expect.objectContaining({ method: 'POST' })),
    )
    expect(await screen.findByText('Disdetta registrata.')).toBeInTheDocument()
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

  it('drops a failed send’s stale alert once a different action on the framework agreement succeeds (REB-407)', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro: OUT, quadri: [OUT] },
      'POST /api/hub/matches/m1/send': () =>
        answer(409, {
          detail:
            'Il testo della lettera di incarico è ancora una bozza (status: draft): si genera e si salva, ma non parte per la firma.',
        }),
      'POST /api/hub/contract-documents/d1/resend': OUT,
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ancora una bozza')

    await userEvent.click(screen.getByRole('button', { name: 'Reinvia email del contratto quadro' }))

    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows a failed «Reinvia email» on the framework agreement inside its own card, not under the matches (REB-407)', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro: OUT, quadri: [OUT] },
      'POST /api/hub/contract-documents/d1/resend': () =>
        answer(503, {
          detail: 'La mail non è partita: il provider l’ha rifiutata. Riprova tra qualche minuto.',
        }),
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Reinvia email del contratto quadro' }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(within(await quadroCard()).getByRole('alert')).toHaveTextContent('non è partita')
  })

  it('shows the next step in progress while it reads Documenso, as «Crea match» does for its own buttons (REB-407)', async () => {
    const late = deferred<Response>()
    const waiting = {
      ...QUADRO,
      ha_pdf_firmato: false,
      situazione: 'Firmato il 1° ottobre 2026; la copia firmata non è ancora arrivata.',
      prossima_azione: 'aggiorna_stato',
      altre_azioni: ['registra_disdetta'],
    }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro: waiting, quadri: [waiting] },
      'POST /api/hub/contract-documents/d1/refresh': () => late.promise,
    })
    mount('/admin/freelance/f1/contracts')
    const button = await screen.findByRole('button', { name: 'Aggiorna stato del contratto quadro' })
    await userEvent.click(button)

    expect(await screen.findByText('Aggiorno…')).toBeInTheDocument()
    expect(button).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Altre azioni del contratto quadro' })).toBeDisabled()

    late.resolve(answer(200, waiting))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(button).toHaveTextContent('Aggiorna stato')
  })

  it('says on «Altre azioni» that an action picked from it is running, since its menu has closed', async () => {
    const late = deferred<Response>()
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro: OUT, quadri: [OUT] },
      'POST /api/hub/contract-documents/d1/refresh': () => late.promise,
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await quadroCard(), 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Aggiorna stato del contratto quadro' }))

    const trigger = screen.getByRole('button', { name: 'Altre azioni del contratto quadro' })
    await waitFor(() => expect(trigger).toHaveTextContent('Aggiorno…'))
    expect(trigger).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reinvia email del contratto quadro' })).toBeDisabled()

    late.resolve(answer(200, OUT))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(trigger).toHaveTextContent('Altre azioni')
  })

  it('keeps the tax data in a closed «Dati fiscali» that says what is saved, and saves them from there', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'PUT /api/hub/freelancers/f1/fiscal': { ...FISCALE, domicilio: 'Corso Como 1, Milano' },
    })
    mount('/admin/freelance/f1/contracts')
    const summary = await screen.findByText('Dati fiscali')
    const details = summary.closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(within(details).getByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')).toBeInTheDocument()

    await userEvent.click(summary)
    expect(details).toHaveAttribute('open')
    const domicilio = within(details).getByLabelText('Domicilio professionale')
    expect(domicilio).toHaveValue('Via Roma 1, Milano')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Corso Como 1, Milano')
    await userEvent.click(within(details).getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(String(put[1]!.body))).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Corso Como 1, Milano',
      pec: null,
    })
  })

  it('says the tax data are missing, and asks for them with empty fields', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, fiscale: null },
    })
    mount('/admin/freelance/f1/contracts')
    const details = (await screen.findByText('Dati fiscali')).closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(within(details).getByText('Mancano')).toBeInTheDocument()
    expect(within(details).getByLabelText('Codice fiscale')).toHaveValue('')
  })

  it('starts a new match from the page', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    const link = await screen.findByRole('link', { name: 'Crea match' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/match\/new$/)
  })

  it('shows the sentence «Crea match» left once, on arrival, until another action speaks', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro: OUT, quadri: [OUT] },
      'POST /api/hub/contract-documents/d1/resend': OUT,
    })
    const router = mount('/admin/freelance/f1/match/new')
    await screen.findByText('nuovo match')
    const sentence = 'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.'
    await act(() =>
      router.navigate({ to: '/admin/freelance/$id/contracts', params: { id: 'f1' }, state: { notice: sentence } }),
    )

    expect(await screen.findByRole('status')).toHaveTextContent(sentence)
    // Taken off the history entry once shown, so a reload does not say it again.
    await waitFor(() => expect(router.state.location.state.notice).toBeUndefined())
    expect(router.state.location.pathname).toBe('/admin/freelance/f1/contracts')
    expect(screen.getByRole('status')).toHaveTextContent(sentence)

    await userEvent.click(screen.getByRole('button', { name: 'Reinvia email del contratto quadro' }))
    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    expect(screen.queryByText(sentence)).toBeNull()
  })
})
