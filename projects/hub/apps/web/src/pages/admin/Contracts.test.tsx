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
import { afterEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import { formatDate } from '@/lib/format'
import { AdminContracts } from './Contracts'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** `list-test-kit.tsx`'s router of fetches, plus a handler that may be a function of the
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

const PERSON = { id: 'f1', first_name: 'Ada', last_name: 'Lovelace', email: 'ada@studio.it' }
// Each fixture carries the sentence and the actions the core's `match_words` answers for
// its state: the page renders them and decides nothing of its own.
const FRAMEWORK = {
  id: 'd1',
  kind: 'framework',
  regime: 'vat_registered',
  label: 'Contratto quadro',
  freelancer_id: 'f1',
  match_id: null,
  number: null,
  text_version: '0.1',
  draft_text: true,
  status: 'signed',
  created_at: '2026-09-23T10:00:00Z',
  created_by: 'a1',
  sent_at: '2026-09-23T11:00:00Z',
  signed_at: '2026-10-01T09:00:00Z',
  notice_at: null,
  cancel_reason: null,
  has_pdf_signed: true,
  active: true,
  renewal: '2027-10-01',
  last_notice_day: '2027-09-01',
  new_version: true,
  situation:
    'Firmato il 1° ottobre 2026. Si rinnova da solo il 1° ottobre 2027; disdetta entro il 1° settembre 2027. Il testo è ancora in bozza.',
  next_action: null,
  other_actions: ['status_refresh', 'record_notice'],
}
const LETTER = {
  ...FRAMEWORK,
  id: 'd2',
  kind: 'letter',
  label: 'Lettera di incarico',
  match_id: 'm1',
  number: '2026-001',
  status: 'generated',
  sent_at: null,
  signed_at: null,
  has_pdf_signed: false,
  active: false,
  renewal: null,
  last_notice_day: null,
  new_version: false,
  situation: 'Pronta, non ancora inviata.',
  next_action: null,
  other_actions: [],
}
const MATCH = {
  id: 'm1',
  freelancer_id: 'f1',
  company_id: 'c1',
  company_name: 'Rossi Studio',
  requested_role: 'Backend developer',
  customer_legal_name: 'Rossi Studio S.r.l.',
  customer_vat_number: '01234567890',
  customer_registered_office: 'Milano',
  status: 'draft',
  created_at: '2026-09-23T10:00:00Z',
  created_by: 'a1',
  cancelled_at: null,
  updated_at: '2026-09-23T10:00:00Z',
  letter: LETTER,
  situation: 'La lettera n. 2026-001 è pronta: il freelance non ha ancora ricevuto nulla.',
  next_action: 'send',
  other_actions: ['cancel'],
  expected_days: null,
  letter_start_date: '2026-10-01',
  letter_end_date: null,
  letter_fee: '450.00',
  pigro_status: null,
  pigro_slug: null,
  pigro_deal_id: null,
  pigro_url: null,
  pigro_linked_at: null,
  pigro_attempted_at: null,
  pigro_error: null,
  pigro_mail_sent_at: null,
}
const TAX = {
  freelancer_id: 'f1',
  tax_code: 'LVLDAA85T50H501Z',
  vat_number: '01234567890',
  domicile: 'Via Roma 1, Milano',
  certified_email: null,
  updated_by: 'a1',
  updated_at: '2026-09-23T10:00:00Z',
}
const PAGE = { freelancer_id: 'f1', framework: FRAMEWORK, occasional_framework: null, frameworks: [FRAMEWORK], matches: [MATCH], tax: TAX }
/** A framework agreement out for signature, waiting for the freelancer. */
const OUT = {
  ...FRAMEWORK,
  status: 'sent',
  draft_text: false,
  active: false,
  signed_at: null,
  has_pdf_signed: false,
  renewal: null,
  last_notice_day: null,
  situation: 'Inviato il 23 settembre 2026: aspetta la firma del freelance.',
  next_action: 'resend_email',
  other_actions: ['status_refresh', 'cancel'],
}

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const contracts = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: AdminContracts,
  })
  const card = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id',
    component: () => <p>scheda</p>,
  })
  const newMatch = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/match/new',
    component: () => <p>nuovo match</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([contracts, card, newMatch])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const frameworkCard = () => screen.findByRole('region', { name: 'Contratto quadro' })
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
    const card = await frameworkCard()
    expect(within(card).getByText('Firmato')).toBeInTheDocument()
    expect(within(card).getByText(FRAMEWORK.situation)).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'PDF del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf',
    )
    expect(within(card).getByRole('link', { name: 'PDF firmato del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf?signed=true',
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
      'GET /api/hub/freelancers/f1/matches': {
        freelancer_id: 'f1',
        framework: null,
        occasional_framework: null,
        frameworks: [],
        matches: [],
        tax: null,
      },
    })
    mount('/admin/freelance/f1/contracts')
    const card = await frameworkCard()
    expect(within(card).getByText('Nessun contratto quadro: parte con il primo match inviato.')).toBeInTheDocument()
    expect(within(card).queryAllByRole('button')).toHaveLength(0)
    expect(screen.getByText('Nessun match per questa persona.')).toBeInTheDocument()
  })

  const FRAMEWORK_CASES = [
    {
      status: 'generated',
      pill: 'Pronto, non inviato',
      situation: 'Parte con «Invia per la firma» sul suo match.',
      next_action: null,
      other_actions: ['cancel'],
      primary: [],
      more: ['Annulla il contratto quadro'],
    },
    {
      status: 'sent',
      pill: 'Da firmare',
      situation: 'Inviato il 23 settembre 2026: aspetta la firma del freelance.',
      next_action: 'resend_email',
      other_actions: ['status_refresh', 'cancel'],
      primary: ['Reinvia email del contratto quadro'],
      more: ['Aggiorna stato del contratto quadro', 'Annulla il contratto quadro'],
    },
    {
      status: 'signed',
      pill: 'Firmato',
      situation: 'Firmato il 1° ottobre 2026; la copia firmata non è ancora arrivata.',
      next_action: 'status_refresh',
      other_actions: ['record_notice'],
      primary: ['Aggiorna stato del contratto quadro'],
      more: ['Registra disdetta del contratto quadro'],
    },
    {
      status: 'cancelled',
      pill: 'Annullato',
      situation: 'Rifiutato dal freelance: il compenso è sbagliato.',
      next_action: null,
      other_actions: [],
      primary: [],
      more: [],
    },
    {
      status: 'notice_served',
      pill: 'Disdetto',
      situation: 'Disdetto il 2 ottobre 2026.',
      next_action: null,
      other_actions: [],
      primary: [],
      more: [],
    },
  ]

  it.each(FRAMEWORK_CASES)(
    'a framework agreement «$pill» reads its sentence and offers exactly the core’s next step',
    async ({ status, pill, situation, next_action, other_actions, primary, more }) => {
      const frameworkDoc = { ...FRAMEWORK, status, situation, next_action, other_actions }
      routeFetch({
        'GET /api/hub/freelancers/f1': PERSON,
        'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: frameworkDoc, frameworks: [frameworkDoc] },
      })
      mount('/admin/freelance/f1/contracts')
      const card = await frameworkCard()
      expect(within(card).getByText(pill)).toBeInTheDocument()
      expect(within(card).getByText(situation)).toBeInTheDocument()
      const trigger = more.length ? ['Altre azioni del contratto quadro'] : []
      expect(buttonNames(card)).toEqual([...primary, ...trigger])
      if (more.length) expect(await openMore(card, 'Altre azioni del contratto quadro')).toEqual(more)
    },
  )

  const MATCH_CASES = [
    {
      name: 'a draft',
      status: 'draft',
      letter: {},
      pill: 'Da inviare',
      situation: MATCH.situation,
      next_action: 'send',
      other_actions: ['cancel'],
      primary: ['Invia per la firma il match con Rossi Studio come Backend developer'],
      more: ['Annulla il match con Rossi Studio come Backend developer'],
    },
    {
      name: 'a letter waiting for a framework agreement out for signature',
      status: 'awaiting_signature',
      letter: { status: 'pending' },
      pill: 'In attesa di firma',
      situation: 'La lettera n. 2026-001 aspetta la firma del contratto quadro e parte da sola dopo.',
      next_action: null,
      other_actions: ['cancel'],
      primary: [],
      more: ['Annulla il match con Rossi Studio come Backend developer'],
    },
    {
      name: 'a letter out for signature',
      status: 'awaiting_signature',
      letter: { status: 'sent', sent_at: '2026-09-24T08:00:00Z' },
      pill: 'In attesa di firma',
      situation: 'Lettera n. 2026-001 inviata il 24 settembre 2026: aspetta la firma del freelance.',
      next_action: 'resend_email',
      other_actions: ['status_refresh', 'cancel'],
      primary: ['Reinvia email della lettera n. 2026-001'],
      more: ['Aggiorna stato della lettera n. 2026-001', 'Annulla il match con Rossi Studio come Backend developer'],
    },
    {
      name: 'an active match',
      status: 'active',
      letter: { status: 'signed', has_pdf_signed: true },
      pill: 'Attivo',
      situation: 'Lettera n. 2026-001 firmata il 1° ottobre 2026, dal 1° ottobre 2026.',
      next_action: null,
      other_actions: ['close'],
      primary: [],
      more: ['Chiudi il match con Rossi Studio come Backend developer'],
    },
    {
      name: 'a closed match',
      status: 'concluded',
      letter: { status: 'signed', has_pdf_signed: true },
      pill: 'Concluso',
      situation: 'Lettera n. 2026-001, dal 1° ottobre 2026 al 31 dicembre 2026.',
      next_action: null,
      other_actions: [],
      primary: [],
      more: [],
    },
    {
      name: 'a match cancelled after the freelancer refused',
      status: 'cancelled',
      letter: { status: 'cancelled', cancel_reason: 'Rifiutato dal freelance: il compenso è sbagliato' },
      pill: 'Annullato',
      situation: 'La lettera n. 2026-001 non va più firmata. Rifiutato dal freelance: il compenso è sbagliato.',
      next_action: null,
      other_actions: [],
      primary: [],
      more: [],
    },
  ]

  it.each(MATCH_CASES)(
    '$name reads its sentence and offers exactly the core’s next step',
    async ({ status, letter, pill, situation, next_action, other_actions, primary, more }) => {
      const match = { ...MATCH, status, letter: { ...LETTER, ...letter }, situation, next_action, other_actions }
      routeFetch({
        'GET /api/hub/freelancers/f1': PERSON,
        'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [match] },
      })
      mount('/admin/freelance/f1/contracts')
      const card = await matchCard()
      expect(within(card).getByText(pill)).toBeInTheDocument()
      expect(within(card).getByText(situation)).toBeInTheDocument()
      const trigger = more.length ? ['Altre azioni del match con Rossi Studio come Backend developer'] : []
      expect(buttonNames(card)).toEqual([...primary, ...trigger])
      if (more.length) expect(await openMore(card, 'Altre azioni del match con Rossi Studio come Backend developer')).toEqual(more)
    },
  )

  it('shows a match’s letter PDFs and when it was created, and never a send on the framework card', async () => {
    const signed = { ...LETTER, status: 'signed', has_pdf_signed: true }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [{ ...MATCH, letter: signed }] },
    })
    mount('/admin/freelance/f1/contracts')
    const card = await matchCard()
    expect(within(card).getByRole('link', { name: 'PDF della lettera n. 2026-001' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d2/pdf',
    )
    expect(within(card).getByRole('link', { name: 'PDF firmato della lettera n. 2026-001' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d2/pdf?signed=true',
    )
    expect(within(card).getByText(`Creato il ${formatDate(MATCH.created_at)}`)).toBeInTheDocument()
    // A framework agreement leaves with a match, so «Invia per la firma» is the match's
    // alone (REB-406).
    expect(within(await frameworkCard()).queryByRole('button', { name: /Invia per la firma/ })).toBeNull()
  })

  it('sends a draft match for signature and says what left (REB-390)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/matches/m1/send': {
        match: { ...MATCH, status: 'awaiting_signature', letter: { ...LETTER, status: 'pending' } },
        sent: 'framework',
        mail_sent: true,
      },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio come Backend developer' }))
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
                  status: 'cancelled',
                  letter: { ...LETTER, status: 'cancelled' },
                  situation: 'La lettera n. 2026-001 non va più firmata.',
                  next_action: null,
                  other_actions: [],
                },
              ],
            }
          : PAGE,
      'POST /api/hub/matches/m1/cancel': () => {
        cancelled = true
        return { ...MATCH, status: 'cancelled' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio come Backend developer')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Annulla il match con Rossi Studio come Backend developer' }))
    expect(await screen.findByRole('dialog', { name: 'Annullare il match?' })).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalledWith('/api/hub/matches/m1/cancel', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Annulla il match' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/cancel', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByText('La lettera n. 2026-001 non va più firmata.')).toBeInTheDocument()
    expect(within(await matchCard()).getByText('Annullato')).toBeInTheDocument()
    // «Altre azioni» went with the match's last action: the focus lands on its card.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Rossi Studio · Backend developer' })).toHaveFocus(),
    )
  })

  it('puts the focus on the «Match» heading when the card itself is gone after a confirmed action', async () => {
    let cancelled = false
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () => (cancelled ? { ...PAGE, matches: [] } : PAGE),
      'POST /api/hub/matches/m1/cancel': () => {
        cancelled = true
        return { ...MATCH, status: 'cancelled' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio come Backend developer')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Annulla il match con Rossi Studio come Backend developer' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla il match' }))
    expect(await screen.findByText('Nessun match per questa persona.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Match', level: 2 })).toHaveFocus())
  })

  it('gives the focus back to «Altre azioni» when the question is dismissed, since the item that asked is gone', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    const card = await frameworkCard()
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
            status: match.status,
            letter: { ...LETTER, ...match.letter },
            situation: match.situation,
            next_action: match.next_action,
            other_actions: match.other_actions,
          },
        ],
      },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio come Backend developer')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Annulla il match con Rossi Studio come Backend developer' }))
    expect(await screen.findByText(/il link ricevuto dal freelance smette di funzionare/)).toBeInTheDocument()
  })

  it('closes an active match from «Altre azioni» only after asking, since the page cannot reopen it', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        matches: [{ ...MATCH, status: 'active', next_action: null, other_actions: ['close'] }],
      },
      'POST /api/hub/matches/m1/close': { ...MATCH, status: 'concluded' },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio come Backend developer')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Chiudi il match con Rossi Studio come Backend developer' }))
    const dialog = await screen.findByRole('dialog', { name: 'Chiudere il match?' })
    expect(dialog).toHaveTextContent('diventa «Concluso»')
    expect(dialog).toHaveTextContent('La lettera n. 2026-001 e il contratto quadro restano come sono.')
    expect(spy).not.toHaveBeenCalledWith('/api/hub/matches/m1/close', expect.anything())
    await userEvent.click(within(dialog).getByRole('button', { name: 'Sì, chiudi il match' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/close', expect.objectContaining({ method: 'POST' })))
  })

  it('names each match by company and role, so two matches with one company read apart', async () => {
    const second = {
      ...MATCH,
      id: 'm2',
      requested_role: 'Designer',
      letter: { ...LETTER, id: 'd3', match_id: 'm2', number: '2026-002' },
    }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [second, MATCH] },
    })
    mount('/admin/freelance/f1/contracts')
    expect(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio come Designer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Invia per la firma il match con Rossi Studio come Backend developer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Altre azioni del match con Rossi Studio come Designer' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Altre azioni del match con Rossi Studio come Backend developer' })).toBeInTheDocument()
  })

  it('lands on the match card a #match-<id> link names, once the contracts have loaded (REB-609)', async () => {
    const second = {
      ...MATCH,
      id: 'm2',
      requested_role: 'Designer',
      letter: { ...LETTER, id: 'd3', match_id: 'm2', number: '2026-002' },
    }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [MATCH, second] },
    })
    const scrolled = vi.fn()
    vi.stubGlobal('scrollTo', vi.fn())
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled(this.id)
    }
    onTestFinished(() => {
      Element.prototype.scrollIntoView = original
    })
    mount('/admin/freelance/f1/contracts#match-m2')

    const heading = await screen.findByRole('heading', { name: 'Rossi Studio · Designer' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(scrolled).toHaveBeenCalledWith('match-m2')
    expect(screen.getByRole('heading', { name: 'Rossi Studio · Backend developer' })).not.toHaveFocus()
  })

  it('leaves out, without failing, an action the card has no call for and one it does not know', async () => {
    const frameworkDoc = { ...OUT, next_action: 'close', other_actions: ['send', 'sospendi', 'status_refresh'] }
    const match = { ...MATCH, next_action: 'sospendi', other_actions: ['record_notice', 'sospendi', 'cancel'] }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: frameworkDoc, frameworks: [frameworkDoc], matches: [match] },
    })
    mount('/admin/freelance/f1/contracts')
    const framework = await frameworkCard()
    expect(buttonNames(framework)).toEqual(['Altre azioni del contratto quadro'])
    expect(await openMore(framework, 'Altre azioni del contratto quadro')).toEqual(['Aggiorna stato del contratto quadro'])
    await userEvent.keyboard('{Escape}')
    const card = await matchCard()
    expect(buttonNames(card)).toEqual(['Altre azioni del match con Rossi Studio come Backend developer'])
    expect(await openMore(card, 'Altre azioni del match con Rossi Studio come Backend developer')).toEqual([
      'Annulla il match con Rossi Studio come Backend developer',
    ])
  })

  it('resends a letter’s mail and reads its state from its match’s card', async () => {
    const out = MATCH_CASES[2]!
    const match = {
      ...MATCH,
      status: out.status,
      letter: { ...LETTER, ...out.letter },
      situation: out.situation,
      next_action: out.next_action,
      other_actions: out.other_actions,
    }
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [match] },
      'POST /api/hub/contract-documents/d2/resend': match.letter,
      'POST /api/hub/contract-documents/d2/refresh': match.letter,
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(within(await matchCard()).getByRole('button', { name: 'Reinvia email della lettera n. 2026-001' }))
    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    await openMore(await matchCard(), 'Altre azioni del match con Rossi Studio come Backend developer')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Aggiorna stato della lettera n. 2026-001' }))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d2/resend', expect.objectContaining({ method: 'POST' }))
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d2/refresh', expect.objectContaining({ method: 'POST' }))
  })

  it('resends the signing mail and refreshes a framework agreement out for signature (REB-407)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: OUT, frameworks: [OUT] },
      'POST /api/hub/contract-documents/d1/resend': OUT,
      'POST /api/hub/contract-documents/d1/refresh': OUT,
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Reinvia email del contratto quadro' }))
    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    await openMore(await frameworkCard(), 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Aggiorna stato del contratto quadro' }))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/resend', expect.objectContaining({ method: 'POST' }))
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/refresh', expect.objectContaining({ method: 'POST' }))
  })

  it('cancels a framework agreement out for signature only after asking, and reads the new sentence', async () => {
    let cancelled = false
    const gone = { ...OUT, status: 'cancelled', cancel_reason: 'Annullato da rebase.', situation: 'Annullato da rebase.', next_action: null, other_actions: [] }
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () => {
        const frameworkDoc = cancelled ? gone : OUT
        return { ...PAGE, framework: frameworkDoc, frameworks: [frameworkDoc] }
      },
      'POST /api/hub/contract-documents/d1/cancel': () => {
        cancelled = true
        return gone
      },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await frameworkCard(), 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Annulla il contratto quadro' }))
    expect(await screen.findByRole('dialog', { name: 'Annullare il contratto quadro?' })).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Sì, annulla il contratto quadro' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.objectContaining({ method: 'POST' })),
    )
    expect(await screen.findByText('Annullato da rebase.')).toBeInTheDocument()
    expect(within(await frameworkCard()).queryByRole('button', { name: /Altre azioni/ })).toBeNull()
  })

  it('records a notice on an active framework agreement only after asking', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/contract-documents/d1/notice': { ...FRAMEWORK, status: 'notice_served', active: false },
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await frameworkCard(), 'Altre azioni del contratto quadro')
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
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio come Backend developer' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ancora una bozza')
  })

  it('drops a failed send’s stale alert once a different action on the framework agreement succeeds (REB-407)', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: OUT, frameworks: [OUT] },
      'POST /api/hub/matches/m1/send': () =>
        answer(409, {
          detail:
            'Il testo della lettera di incarico è ancora una bozza (status: draft): si genera e si salva, ma non parte per la firma.',
        }),
      'POST /api/hub/contract-documents/d1/resend': OUT,
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio come Backend developer' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ancora una bozza')

    await userEvent.click(screen.getByRole('button', { name: 'Reinvia email del contratto quadro' }))

    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows a failed «Reinvia email» on the framework agreement inside its own card, not under the matches (REB-407)', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: OUT, frameworks: [OUT] },
      'POST /api/hub/contract-documents/d1/resend': () =>
        answer(503, {
          detail: 'La mail non è partita: il provider l’ha rifiutata. Riprova tra qualche minuto.',
        }),
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Reinvia email del contratto quadro' }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(within(await frameworkCard()).getByRole('alert')).toHaveTextContent('non è partita')
  })

  it('shows the next step in progress while it reads Documenso, as «Crea match» does for its own buttons (REB-407)', async () => {
    const late = deferred<Response>()
    const waiting = {
      ...FRAMEWORK,
      has_pdf_signed: false,
      situation: 'Firmato il 1° ottobre 2026; la copia firmata non è ancora arrivata.',
      next_action: 'status_refresh',
      other_actions: ['record_notice'],
    }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: waiting, frameworks: [waiting] },
      'POST /api/hub/contract-documents/d1/refresh': () => late.promise,
    })
    mount('/admin/freelance/f1/contracts')
    const button = await screen.findByRole('button', { name: 'Aggiorna stato del contratto quadro' })
    await userEvent.click(button)

    expect(await screen.findByText('Aggiorno…')).toBeInTheDocument()
    expect(button).toBeDisabled()
    // What a screen reader hears says the same progress as the words on the button.
    expect(button).toHaveAccessibleName('Aggiorno lo stato del contratto quadro')
    expect(screen.getByRole('button', { name: 'Altre azioni del contratto quadro' })).toBeDisabled()

    late.resolve(answer(200, waiting))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(button).toHaveTextContent('Aggiorna stato')
    expect(button).toHaveAccessibleName('Aggiorna stato del contratto quadro')
  })

  it('says on «Altre azioni» that an action picked from it is running, since its menu has closed', async () => {
    const late = deferred<Response>()
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: OUT, frameworks: [OUT] },
      'POST /api/hub/contract-documents/d1/refresh': () => late.promise,
    })
    mount('/admin/freelance/f1/contracts')
    const trigger = within(await frameworkCard()).getByRole('button', { name: 'Altre azioni del contratto quadro' })
    await openMore(await frameworkCard(), 'Altre azioni del contratto quadro')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Aggiorna stato del contratto quadro' }))

    await waitFor(() => expect(trigger).toHaveTextContent('Aggiorno…'))
    expect(trigger).toHaveAccessibleName('Aggiorno lo stato del contratto quadro')
    expect(trigger).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reinvia email del contratto quadro' })).toBeDisabled()

    late.resolve(answer(200, OUT))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(trigger).toHaveTextContent('Altre azioni')
    expect(trigger).toHaveAccessibleName('Altre azioni del contratto quadro')
  })

  it('keeps the tax data in a closed «Dati fiscali» that says what is saved, and saves them from there', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'PUT /api/hub/freelancers/f1/fiscal': { ...TAX, domicile: 'Corso Como 1, Milano' },
    })
    mount('/admin/freelance/f1/contracts')
    const summary = await screen.findByText('Dati fiscali')
    const details = summary.closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(within(details).getByText('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')).toBeInTheDocument()

    await userEvent.click(summary)
    expect(details).toHaveAttribute('open')
    const domicile = within(details).getByLabelText('Domicilio')
    expect(domicile).toHaveValue('Via Roma 1, Milano')
    await userEvent.clear(domicile)
    await userEvent.type(domicile, 'Corso Como 1, Milano')
    await userEvent.click(within(details).getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(String(put[1]!.body))).toEqual({
      tax_code: 'LVLDAA85T50H501Z',
      vat_number: '01234567890',
      domicile: 'Corso Como 1, Milano',
      certified_email: null,
    })
  })

  it('says the tax data are missing, and asks for them with empty fields', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, tax: null },
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
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: OUT, frameworks: [OUT] },
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

  it('drops the page’s last sentence when the tax data start saving, as any other action does', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'PUT /api/hub/freelancers/f1/fiscal': TAX,
    })
    const router = mount('/admin/freelance/f1/match/new')
    await screen.findByText('nuovo match')
    const sentence = 'Bozza salvata: la trovi qui sotto, da inviare.'
    await act(() =>
      router.navigate({ to: '/admin/freelance/$id/contracts', params: { id: 'f1' }, state: { notice: sentence } }),
    )
    expect(await screen.findByText(sentence)).toBeInTheDocument()

    await userEvent.click(screen.getByText('Dati fiscali'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    expect(screen.queryByText(sentence)).toBeNull()
  })
})

describe('a match’s link to Pigro on its card (REB-502)', () => {
  const SIGNED = 'Lettera n. 2026-001 firmata il 1° ottobre 2026, dal 1° ottobre 2026.'
  const ACTIVE = {
    ...MATCH,
    status: 'active',
    letter: { ...LETTER, status: 'signed', has_pdf_signed: true },
    next_action: null,
    expected_days: 40,
  }
  const LINKED = {
    ...ACTIVE,
    pigro_status: 'linked',
    pigro_slug: 'ada',
    pigro_deal_id: '6f1c2d3e-0000-4000-8000-000000000009',
    pigro_url: 'https://pigro.letsrebase.com/ada/app/deal/6f1c2d3e-0000-4000-8000-000000000009',
    pigro_linked_at: '2026-10-01T09:05:00Z',
    situation: `${SIGNED} Le ore si consuntivano su Pigro.`,
    other_actions: ['close'],
  }
  const FAILED = {
    ...ACTIVE,
    pigro_status: 'error',
    pigro_error: 'HTTP 503',
    pigro_attempted_at: '2026-10-01T09:05:00Z',
    situation: `${SIGNED} Pigro non ha risposto: HTTP 503.`,
    other_actions: ['close', 'retry_pigro'],
  }
  const MORE = 'Altre azioni del match con Rossi Studio come Backend developer'
  const RETRY = 'Riprova su Pigro per il match con Rossi Studio come Backend developer'
  const REPORT = 'Consuntivo del match con Rossi Studio come Backend developer'

  it('says in the card’s sentence that the hours are on Pigro, and opens «Consuntivo» beside the PDFs', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [LINKED] },
    })
    mount('/admin/freelance/f1/contracts')
    const card = await matchCard()
    expect(within(card).getByText(LINKED.situation)).toBeInTheDocument()
    const report = within(card).getByRole('link', { name: REPORT })
    expect(report).toHaveTextContent(/^Consuntivo$/)
    expect(report.getAttribute('href')).toMatch(/\/admin\/matches\/m1\/report$/)
    // Beside the letter's PDFs, among the card's own steps.
    const pdf = within(card).getByRole('link', { name: 'PDF firmato della lettera n. 2026-001' })
    expect(pdf.parentElement).toBe(report.parentElement)
    expect(await openMore(card, MORE)).toEqual(['Chiudi il match con Rossi Studio come Backend developer'])
  })

  it.each([
    { pigro_status: null, situation: SIGNED, other_actions: ['close'] },
    { pigro_status: 'to_link', situation: `${SIGNED} Pigro non ha ancora il deal: riprova o aspetta lo sweep.`, other_actions: ['close', 'retry_pigro'] },
    { pigro_status: 'error', situation: FAILED.situation, other_actions: ['close', 'retry_pigro'] },
    {
      pigro_status: 'rejected',
      situation: `${SIGNED} Pigro ha rifiutato il collegamento: Il deal esiste già.`,
      other_actions: ['close', 'retry_pigro'],
    },
  ])('offers no «Consuntivo» for a match whose link is $pigro_stato, and says why', async (state) => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [{ ...ACTIVE, ...state }] },
    })
    mount('/admin/freelance/f1/contracts')
    const card = await matchCard()
    expect(within(card).getByText(state.situation)).toBeInTheDocument()
    expect(within(card).queryByRole('link', { name: /Consuntivo/ })).toBeNull()
  })

  it('links the match now from «Altre azioni» with «Riprova su Pigro», and reads the card again', async () => {
    let linked = false
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () => ({ ...PAGE, matches: [linked ? LINKED : FAILED] }),
      'POST /api/hub/matches/m1/pigro/link': () => {
        linked = true
        return LINKED
      },
    })
    mount('/admin/freelance/f1/contracts')
    const card = await matchCard()
    expect(within(card).getByText(FAILED.situation)).toBeInTheDocument()
    expect(await openMore(card, MORE)).toEqual(['Chiudi il match con Rossi Studio come Backend developer', RETRY])
    await userEvent.click(screen.getByRole('menuitem', { name: RETRY }))

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/pigro/link', expect.objectContaining({ method: 'POST' })),
    )
    expect(await within(card).findByText(LINKED.situation)).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: REPORT })).toBeInTheDocument()
    // It asks nothing first: linking again takes nothing back.
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says «Collego a Pigro…» on «Altre azioni» while the CRM answers, which can take a while', async () => {
    const late = deferred<Response>()
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [FAILED] },
      'POST /api/hub/matches/m1/pigro/link': () => late.promise,
    })
    mount('/admin/freelance/f1/contracts')
    const card = await matchCard()
    const trigger = within(card).getByRole('button', { name: MORE })
    await openMore(card, MORE)
    await userEvent.click(screen.getByRole('menuitem', { name: RETRY }))

    await waitFor(() => expect(trigger).toHaveTextContent('Collego a Pigro…'))
    expect(trigger).toHaveAccessibleName('Collego a Pigro il match con Rossi Studio come Backend developer')
    expect(trigger).toBeDisabled()

    late.resolve(answer(200, FAILED))
    await waitFor(() => expect(trigger).toHaveTextContent('Altre azioni'))
    expect(trigger).toBeEnabled()
  })

  it('shows the server’s sentence under the matches when the link cannot run here', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [FAILED] },
      'POST /api/hub/matches/m1/pigro/link': () =>
        answer(503, { detail: 'Consuntivo non configurato su questo ambiente.' }),
    })
    mount('/admin/freelance/f1/contracts')
    await openMore(await matchCard(), MORE)
    await userEvent.click(screen.getByRole('menuitem', { name: RETRY }))
    const section = screen.getByRole('region', { name: 'Match' })
    expect(await within(section).findByRole('alert')).toHaveTextContent('Consuntivo non configurato su questo ambiente.')
  })
})

describe('one framework agreement per regime (REB-687)', () => {
  const OCCASIONAL = { ...OUT, id: 'd9', regime: 'occasional', label: 'Contratto quadro per prestazioni occasionali' }
  const LETTER_OCCASIONAL = {
    ...LETTER,
    id: 'd8',
    number: '2026-002',
    regime: 'occasional',
    label: 'Lettera di incarico occasionale',
  }
  const MATCH_OCCASIONAL = { ...MATCH, id: 'm2', company_name: 'Verdi Snc', letter: LETTER_OCCASIONAL }
  const BOTH = {
    ...PAGE,
    occasional_framework: OCCASIONAL,
    frameworks: [OCCASIONAL, FRAMEWORK],
    matches: [MATCH_OCCASIONAL, MATCH],
  }

  it('shows a card for each, named by its type, each with its own actions', async () => {
    const gone = { ...OCCASIONAL, status: 'cancelled', next_action: null, other_actions: [] }
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': BOTH,
      'POST /api/hub/contract-documents/d9/cancel': gone,
    })
    mount('/admin/freelance/f1/contracts')
    const ordinary = await screen.findByRole('region', { name: 'Contratto quadro' })
    const occasional = screen.getByRole('region', { name: 'Contratto quadro per prestazioni occasionali' })
    expect(within(ordinary).getByText(FRAMEWORK.situation)).toBeInTheDocument()
    expect(
      within(occasional).getByRole('link', { name: 'PDF del contratto quadro per prestazioni occasionali' }),
    ).toHaveAttribute('href', '/api/hub/contract-documents/d9/pdf')
    expect(
      within(occasional).getByRole('button', { name: 'Reinvia email del contratto quadro per prestazioni occasionali' }),
    ).toBeInTheDocument()
    await openMore(occasional, 'Altre azioni del contratto quadro per prestazioni occasionali')
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'Annulla il contratto quadro per prestazioni occasionali' }),
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Sì, annulla il contratto quadro per prestazioni occasionali' }),
    )
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d9/cancel', expect.objectContaining({ method: 'POST' })),
    )
    expect(spy).not.toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.anything())
  })

  it('asks about an occasional framework agreement by its own type before cancelling it', async () => {
    const spy = routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': BOTH })
    mount('/admin/freelance/f1/contracts')
    const occasional = await screen.findByRole('region', { name: 'Contratto quadro per prestazioni occasionali' })
    await openMore(occasional, 'Altre azioni del contratto quadro per prestazioni occasionali')
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'Annulla il contratto quadro per prestazioni occasionali' }),
    )
    expect(
      await screen.findByRole('dialog', { name: 'Annullare il contratto quadro per prestazioni occasionali?' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Sì, annulla il contratto quadro per prestazioni occasionali' }),
    ).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalledWith('/api/hub/contract-documents/d9/cancel', expect.anything())
  })

  it('says in the notice question that the occasional framework agreement is the one no longer active', async () => {
    const active = {
      ...FRAMEWORK,
      id: 'd9',
      regime: 'occasional',
      label: 'Contratto quadro per prestazioni occasionali',
      draft_text: false,
      new_version: false,
    }
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, framework: null, occasional_framework: active, frameworks: [active] },
    })
    mount('/admin/freelance/f1/contracts')
    const occasional = await screen.findByRole('region', { name: 'Contratto quadro per prestazioni occasionali' })
    await openMore(occasional, 'Altre azioni del contratto quadro per prestazioni occasionali')
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'Registra disdetta del contratto quadro per prestazioni occasionali' }),
    )
    expect(await screen.findByRole('dialog', { name: 'Registrare la disdetta?' })).toHaveAccessibleDescription(
      'Da oggi il contratto quadro per prestazioni occasionali non è più attivo, e il prossimo match ne genera uno nuovo. Si registra quando il freelance o rebase ha dato disdetta, o uno dei due ha receduto.',
    )
  })

  it('lands on a heading still on the page once a cancelled occasional framework agreement that never left is gone', async () => {
    const unsent = {
      ...OCCASIONAL,
      status: 'generated',
      sent_at: null,
      situation: 'Parte con «Invia per la firma» sul suo match.',
      next_action: null,
      other_actions: ['cancel'],
    }
    let cancelled = false
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      // Never sent, so once cancelled it is hidden: no framework card is left but the
      // ordinary one saying there is none.
      'GET /api/hub/freelancers/f1/matches': () => {
        const frameworks = [cancelled ? { ...unsent, status: 'cancelled' } : unsent]
        return { ...PAGE, framework: null, occasional_framework: cancelled ? null : unsent, frameworks, matches: [] }
      },
      'POST /api/hub/contract-documents/d9/cancel': () => {
        cancelled = true
        return { ...unsent, status: 'cancelled' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    const occasional = await screen.findByRole('region', { name: 'Contratto quadro per prestazioni occasionali' })
    await openMore(occasional, 'Altre azioni del contratto quadro per prestazioni occasionali')
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'Annulla il contratto quadro per prestazioni occasionali' }),
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Sì, annulla il contratto quadro per prestazioni occasionali' }),
    )
    expect(await screen.findByText('Nessun contratto quadro: parte con il primo match inviato.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Contratto quadro per prestazioni occasionali' })).toBeNull()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Contratto quadro', level: 2 })).toHaveFocus())
  })

  it('names each match’s letter by its type', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': BOTH })
    mount('/admin/freelance/f1/contracts')
    const occasional = await screen.findByRole('article', { name: 'Verdi Snc · Backend developer' })
    expect(within(occasional).getByText('Lettera di incarico occasionale n. 2026-002')).toBeInTheDocument()
    expect(within(await matchCard()).getByText('Lettera di incarico n. 2026-001')).toBeInTheDocument()
  })
})
