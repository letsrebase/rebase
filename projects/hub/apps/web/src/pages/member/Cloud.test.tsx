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
import { afterEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { Cloud } from './Cloud'

// Escapes on purpose: the space before «€» is a non-breaking one, the dash an en dash.
const NBSP = ' '
const DASH = '–'

const THANKS = 'Grazie: ti scriviamo entro due giorni lavorativi.'
const CLOSED = 'Il talent cloud non è aperto per questo account.'
const DESCRIZIONE =
  'Ci serve una web app per i clienti: pagamenti, dashboard dei conti, API di open banking.'

const ADA_ID = '9d0c1a2b-1111-4111-8111-000000000001'
const GRACE_ID = '9d0c1a2b-2222-4222-8222-000000000002'

/** Two talents as the API answers them, plus what the page must never show: the
 *  fixture carries a rate, a state, a note, an address and a phone the API does not
 *  send, so a page that rendered any of them fails here rather than in front of a
 *  company. */
const ADA = {
  freelancer_id: ADA_ID,
  nome: 'Ada',
  cognome: 'Lovelace',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  links: ['https://github.com/ada', 'javascript:alert(1)'],
  vetted: true,
  card: {
    ruolo: 'Backend developer',
    seniority: 'senior',
    anni: 9,
    competenze: ['Python', 'FastAPI'],
    settori: ['fintech'],
    lingue: ['italiano', 'inglese'],
    luogo: null,
    sintesi: 'Nove anni su sistemi di pagamento, dal disegno delle API al rilascio.',
  },
  modalita: 'remoto',
  fascia: { min: 500, max: 650 },
  ha_cv: true,
  tariffa_giornaliera: '463.21',
  stato: 'attivo',
  note: 'Nota riservata dell’admin',
  email: 'ada@studio.it',
  telefono: '+39 333 1112233',
}

const GRACE = {
  freelancer_id: GRACE_ID,
  nome: 'Grace',
  cognome: 'Hopper',
  linkedin_url: null,
  links: [],
  vetted: false,
  card: {
    ruolo: 'Frontend developer',
    seniority: 'mid',
    anni: 1,
    competenze: ['React'],
    settori: [],
    lingue: ['italiano'],
    luogo: null,
    sintesi: 'Interfacce in React per prodotti rivolti ai clienti finali.',
  },
  modalita: null,
  fascia: null,
  ha_cv: false,
}

const LIST = { items: [ADA, GRACE], ruoli: ['Backend developer', 'Frontend developer'], capped: false }

const PROPOSAL = {
  id: '5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd',
  riassunto: 'Una web app per i clienti di una fintech: chi fa il backend.',
  luogo: { locale: false, dove: null },
  team: [
    {
      posizione: 1,
      freelancer_id: ADA_ID,
      nome: 'Ada',
      cognome: 'Lovelace',
      ruolo: 'Backend developer',
      motivazione: 'Ha costruito le API di pagamento di due banche.',
      giorni_settimana: 5,
      scheda: { ...ADA.card, luogo: 'Torino' },
      modalita: 'remoto',
      fascia: { min: 500, max: 650 },
    },
  ],
  economia: { giorno: { min: 500, max: 650 }, mese: { min: 11000, max: 14300 }, giorni_mese: 22 },
  previous_id: null,
  origine: 'cloud',
  created_at: '2026-09-26T08:00:00Z',
}

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (url: URL, init: RequestInit | undefined) => Response | Promise<Response>

/** `fetch`, answered by path: the talents' list by default, anything else a 500 so an
 *  unexpected call fails loudly. */
function serve(handlers: Record<string, Handler> = {}): MockInstance<typeof fetch> {
  const routes: Record<string, Handler> = {
    '/api/hub/me/cloud/talents': () => answer(200, LIST),
    ...handlers,
  }
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'https://letsrebase.com')
    const handler = routes[url.pathname]
    return handler ? handler(url, init) : answer(500, { detail: `unexpected ${url.pathname}` })
  })
}

function talentCalls(spy: MockInstance<typeof fetch>): URLSearchParams[] {
  return spy.mock.calls
    .map(([input]) => new URL(String(input), 'https://letsrebase.com'))
    .filter((url) => url.pathname === '/api/hub/me/cloud/talents')
    .map((url) => url.searchParams)
}

function posted(spy: MockInstance<typeof fetch>, path: string): unknown[] {
  return spy.mock.calls
    .filter(([input]) => String(input) === path)
    .map(([, init]) => JSON.parse((init as RequestInit).body as string))
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => root, path: '/me/cloud', component: Cloud })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const router = createRouter({
    routeTree: root.addChildren([page, me]),
    history: createMemoryHistory({ initialEntries: ['/me/cloud'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

/** Opens the `Select` a label names and picks one of its options. */
async function pick(user: ReturnType<typeof userEvent.setup>, label: string, option: string) {
  await user.click(await screen.findByRole('combobox', { name: label }))
  await user.click(await screen.findByRole('option', { name: option }))
}

function card(name: string): HTMLElement {
  return screen.getByRole('heading', { name }).closest('li') as HTMLElement
}

afterEach(() => vi.restoreAllMocks())

describe('Talent cloud, its states', () => {
  it('says it is loading, then shows the builder on top and the profiles under it', async () => {
    serve()
    mount()
    expect(await screen.findByText('Carico i profili…')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Talent cloud', level: 1 })).toBeInTheDocument()
    const headings = screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)
    expect(headings).toEqual(['Proponi un team', 'I profili'])
    expect(screen.getByLabelText('Descrizione del progetto')).toBeInTheDocument()
    expect(screen.getByText('2 profili')).toBeInTheDocument()
  })

  it('shows the API’s sentence and no builder when the cloud is not open for the account', async () => {
    serve({ '/api/hub/me/cloud/talents': () => answer(403, { detail: CLOSED }) })
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent(CLOSED)
    expect(screen.getByRole('link', { name: 'Torna alla tua area' })).toHaveAttribute('href', '/me')
    expect(screen.queryByLabelText('Descrizione del progetto')).toBeNull()
  })

  it('offers «Riprova» when the list could not be read', async () => {
    let calls = 0
    serve({
      '/api/hub/me/cloud/talents': () => {
        calls += 1
        return calls === 1 ? answer(500, { detail: 'Qualcosa è andato storto.' }) : answer(200, LIST)
      },
    })
    const user = userEvent.setup()
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('Qualcosa è andato storto.')
    await user.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(await screen.findByRole('heading', { name: 'Ada Lovelace' })).toBeInTheDocument()
  })

  it('says so when the cloud has nobody yet', async () => {
    serve({ '/api/hub/me/cloud/talents': () => answer(200, { items: [], ruoli: [], capped: false }) })
    mount()
    expect(await screen.findByText('Nel talent cloud non c’è ancora nessun profilo.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Togli i filtri' })).toBeNull()
  })

  it('says the cloud has more than it shows when the list was capped', async () => {
    serve({ '/api/hub/me/cloud/talents': () => answer(200, { ...LIST, capped: true }) })
    mount()
    expect(
      await screen.findByText('Qui trovi i primi 200 profili: usa i filtri per vedere gli altri.'),
    ).toBeInTheDocument()
  })
})

describe('Talent cloud, the cards', () => {
  it('shows each talent by name with the links, the CV, the card, the mode, the band and the vetted badge', async () => {
    serve()
    mount()
    await screen.findByRole('heading', { name: 'Ada Lovelace' })

    const ada = within(card('Ada Lovelace'))
    expect(ada.getByText('Backend developer, Senior, 9 anni di esperienza')).toBeInTheDocument()
    expect(ada.getByText('Verificato da rebase')).toBeInTheDocument()
    expect(ada.getByText(ADA.card.sintesi)).toBeInTheDocument()
    expect(within(ada.getByRole('list', { name: 'Competenze' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['Python', 'FastAPI'])
    expect(within(ada.getByRole('list', { name: 'Settori' })).getByText('fintech')).toBeInTheDocument()
    expect(within(ada.getByRole('list', { name: 'Lingue' })).getByText('inglese')).toBeInTheDocument()
    expect(ada.getByText('Da remoto')).toBeInTheDocument()
    expect(ada.getByText((_, element) => element?.textContent === `500${DASH}650${NBSP}€ al giorno`)).toBeInTheDocument()
    expect(ada.getByRole('link', { name: 'Apri il CV di Ada Lovelace' })).toHaveAttribute(
      'href',
      `/api/hub/me/cloud/talents/${ADA_ID}/cv`,
    )
    expect(ada.getByRole('link', { name: 'LinkedIn' })).toHaveAttribute('href', ADA.linkedin_url)
    expect(ada.getByRole('link', { name: 'github.com' })).toHaveAttribute('href', 'https://github.com/ada')
    // A link that is not http(s) is not one this page opens.
    expect(ada.queryByRole('link', { name: /alert/ })).toBeNull()

    const grace = within(card('Grace Hopper'))
    expect(grace.queryByText('Verificato da rebase')).toBeNull()
    expect(grace.getByText('Tariffa da definire')).toBeInTheDocument()
    expect(grace.queryByRole('link', { name: /Apri il CV/ })).toBeNull()
    expect(grace.queryByRole('list', { name: 'Settori' })).toBeNull()

    // Vetted first, as the API ordered them, and never the rate, the state or the notes.
    const names = screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)
    expect(names.slice(-2)).toEqual(['Ada Lovelace', 'Grace Hopper'])
    const page = document.body.textContent ?? ''
    for (const secret of ['463', 'attivo', 'Nota riservata', 'ada@studio.it', '+39', 'Torino']) {
      expect(page).not.toContain(secret)
    }
  })
})

describe('Talent cloud, the filters', () => {
  it('sends each filter as the API takes it, and clears them all at once', async () => {
    const spy = serve({
      '/api/hub/me/cloud/talents': (url) =>
        url.searchParams.get('competenza') === 'cobol' ? answer(200, { ...LIST, items: [] }) : answer(200, LIST),
    })
    const user = userEvent.setup()
    mount()
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    expect(talentCalls(spy)[0]?.toString()).toBe('')

    await pick(user, 'Ruolo', 'Frontend developer')
    await waitFor(() => expect(talentCalls(spy).at(-1)?.get('ruolo')).toBe('Frontend developer'))
    await pick(user, 'Seniority', 'Senior')
    await waitFor(() => expect(talentCalls(spy).at(-1)?.get('seniority')).toBe('senior'))
    await pick(user, 'Modalità', 'Ibrido')
    await waitFor(() => expect(talentCalls(spy).at(-1)?.get('modalita')).toBe('ibrido'))
    await pick(user, 'Fascia', `500${DASH}650${NBSP}€ al giorno`)
    await waitFor(() => {
      const last = talentCalls(spy).at(-1)
      expect([last?.get('fascia_min'), last?.get('fascia_max')]).toEqual(['500', '650'])
    })
    // The band with no top sends its bottom alone.
    await pick(user, 'Fascia', `oltre 800${NBSP}€ al giorno`)
    await waitFor(() => {
      const last = talentCalls(spy).at(-1)
      expect([last?.get('fascia_min'), last?.has('fascia_max')]).toEqual(['800', false])
    })

    // One skill, sent once the typing stops.
    await user.type(screen.getByLabelText('Competenza'), 'cobol')
    await waitFor(() => expect(talentCalls(spy).at(-1)?.get('competenza')).toBe('cobol'))
    expect(talentCalls(spy).filter((params) => params.has('competenza'))).toHaveLength(1)
    expect(await screen.findByText('Nessun profilo corrisponde ai filtri.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Togli i filtri' }))
    await waitFor(() => expect(talentCalls(spy).at(-1)?.toString()).toBe(''))
    expect(await screen.findByRole('heading', { name: 'Ada Lovelace' })).toBeInTheDocument()
    expect(screen.getByLabelText('Competenza')).toHaveValue('')
  })

  it('lists the roles the API gives, and «Tutti» first', async () => {
    serve()
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByRole('combobox', { name: 'Ruolo' }))
    const options = (await screen.findAllByRole('option')).map((option) => option.textContent)
    expect(options).toEqual(['Tutti', 'Backend developer', 'Frontend developer'])
  })
})

describe('Talent cloud, «Richiedi»', () => {
  it('files a request for that talent with no form and says thanks in a toast', async () => {
    const spy = serve({ '/api/hub/me/cloud/requests': () => answer(201, { id: 'r1' }) })
    const user = userEvent.setup()
    mount()
    await screen.findByRole('heading', { name: 'Ada Lovelace' })

    await user.click(within(card('Ada Lovelace')).getByRole('button', { name: 'Richiedi Ada Lovelace' }))

    expect(await screen.findByText(THANKS)).toBeInTheDocument()
    expect(posted(spy, '/api/hub/me/cloud/requests')).toEqual([{ freelancer_id: ADA_ID }])
    const sent = within(card('Ada Lovelace')).getByRole('button', { name: 'Richiesta inviata per Ada Lovelace' })
    expect(sent).toBeDisabled()
    expect(sent).toHaveTextContent('Richiesta inviata')
    // The other card is untouched.
    expect(within(card('Grace Hopper')).getByRole('button', { name: 'Richiedi Grace Hopper' })).toBeEnabled()
  })

  it('shows the API’s sentence in a toast when the request is refused, and lets it be asked again', async () => {
    serve({ '/api/hub/me/cloud/requests': () => answer(404, { detail: 'Profilo non disponibile.' }) })
    const user = userEvent.setup()
    mount()
    await screen.findByRole('heading', { name: 'Ada Lovelace' })

    await user.click(within(card('Ada Lovelace')).getByRole('button', { name: 'Richiedi Ada Lovelace' }))

    expect(await screen.findByText('Profilo non disponibile.')).toBeInTheDocument()
    expect(within(card('Ada Lovelace')).getByRole('button', { name: 'Richiedi Ada Lovelace' })).toBeEnabled()
  })
})

describe('Talent cloud, the builder inside', () => {
  it('proposes through the cloud’s route and «Assumi team» files the request at once, with the thanks', async () => {
    const spy = serve({
      '/api/hub/me/cloud/proposals': () => answer(200, PROPOSAL),
      '/api/hub/me/cloud/requests': () => answer(201, { id: 'r2' }),
    })
    const user = userEvent.setup()
    mount()
    await user.type(await screen.findByLabelText('Descrizione del progetto'), DESCRIZIONE)
    await user.click(screen.getByRole('button', { name: 'Proponi il team' }))
    await screen.findByText(PROPOSAL.riassunto)
    expect(posted(spy, '/api/hub/me/cloud/proposals')).toEqual([{ descrizione: DESCRIZIONE }])
    expect(posted(spy, '/api/hub/team/proposals')).toEqual([])
    // The cloud shows who each person is: the builder's card is headed by the name.
    const team = screen.getByRole('list', { name: 'Il team' })
    expect(within(team).getByRole('heading', { name: 'Ada Lovelace', level: 3 })).toBeInTheDocument()
    expect(within(team).getByRole('listitem')).toHaveTextContent('Ada LovelaceBackend developer')

    await user.click(screen.getByRole('button', { name: 'Assumi team' }))

    expect(await screen.findByRole('status')).toHaveTextContent(THANKS)
    expect(posted(spy, '/api/hub/me/cloud/requests')).toEqual([{ proposal_id: PROPOSAL.id }])
    expect(screen.queryByLabelText('Azienda')).toBeNull()
    expect(screen.queryByLabelText('Telefono')).toBeNull()
  })
})
