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
import { AdminRichiestaTeam } from './RichiestaTeam'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

// Written as escapes on purpose, as `bands.test.ts` does: an en dash looks like a hyphen.
const DASH = '\u2013'

const SCHEDA = {
  ruolo: 'Backend developer',
  seniority: 'senior',
  anni: 9,
  competenze: ['Python', 'FastAPI'],
  settori: ['fintech'],
  lingue: ['italiano', 'inglese'],
  luogo: 'Torino',
  sintesi: 'Backend developer senior, nove anni fra fintech ed e-commerce.',
}

/** A public request as `GET /api/hub/team/requests/{id}` answers it: the proposal with
 *  the talents' ids, the talents by name with their own rates, the contacts. */
const REQUEST = {
  id: 'r1',
  proposal: {
    id: 'p1',
    riassunto: 'Una web app per i clienti di una fintech, con i pagamenti.',
    luogo: { locale: true, dove: 'Milano' },
    team: [
      {
        posizione: 1,
        freelancer_id: 'f1',
        ruolo: 'Backend developer',
        motivazione: 'Ha scritto API di pagamento per tre anni.',
        giorni_settimana: 5,
        scheda: SCHEDA,
        modalita: 'ibrido',
        fascia: { min: 500, max: 650 },
      },
      {
        posizione: 2,
        freelancer_id: 'f2',
        ruolo: 'Frontend developer',
        motivazione: 'React e TypeScript da sei anni.',
        giorni_settimana: 3,
        scheda: { ...SCHEDA, ruolo: 'Frontend developer' },
        modalita: 'remoto',
        fascia: null,
      },
    ],
    economia: { giorno: null, mese: null, giorni_mese: 22 },
    previous_id: null,
    origine: 'pubblico',
    created_at: '2026-09-25T09:00:00Z',
  },
  riassunto: 'Una web app per i clienti di una fintech, con i pagamenti.',
  descrizione: 'Siamo Acme e ci serve una web app per i nostri clienti, sei mesi, in sede a Milano.',
  origine: 'pubblico',
  azienda: 'Acme S.r.l.',
  email: 'anna@acme.it',
  telefono: '+39 02 1234567',
  user_id: null,
  company_id: null,
  stato: 'nuova',
  note: null,
  talenti: [
    {
      freelancer_id: 'f1',
      nome: 'Ada',
      cognome: 'Lovelace',
      ruolo: 'Backend developer',
      tariffa_giornaliera: '450.00',
      fascia: { min: 500, max: 650 },
      mail_sent_at: null,
      risposta: null,
      risposta_at: null,
    },
    {
      freelancer_id: 'f2',
      nome: 'Grace',
      cognome: 'Hopper',
      ruolo: 'Frontend developer',
      tariffa_giornaliera: null,
      fascia: null,
      mail_sent_at: '2026-09-25T12:00:00Z',
      risposta: 'si',
      risposta_at: '2026-09-26T09:30:00Z',
    },
  ],
  contacted_at: null,
  closed_at: null,
  created_at: '2026-09-25T10:00:00Z',
}

/** The pathless `signedIn` id and the two admin routes this page links to, stubbed. */
function mount(path = '/admin/team/r1') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => signedIn, path: '/admin/team/$id', component: AdminRichiestaTeam })
  const list = createRoute({ getParentRoute: () => signedIn, path: '/admin/team', component: () => <p>lista</p> })
  const talent = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id',
    component: () => <p>talento</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([page, list, talent])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

/** Answers the page's read, and hands every write to `write` with its parsed body. */
function serve(write: (method: string, url: string, body: unknown) => Response = () => answer(500, {})) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (method === 'GET' && url === '/api/hub/team/requests/r1') return answer(200, REQUEST)
    if (method === 'GET') throw new Error(`unhandled fetch in this test: ${method} ${url}`)
    return write(method, url, JSON.parse((init?.body as string | undefined) ?? 'null'))
  })
}

function cellUnder(row: HTMLElement, header: string): HTMLElement {
  const table = row.closest('table')!
  const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
  const index = headers.indexOf(header)
  expect(index).toBeGreaterThanOrEqual(0)
  return within(row).getAllByRole('cell')[index]!
}

afterEach(() => vi.restoreAllMocks())

describe('a team request, as the admin reads it (REB-514, spec § 3.5)', () => {
  it('shows the description, the summary in a box, the place, the contacts and the state', async () => {
    serve()
    mount()

    expect(await screen.findByRole('heading', { level: 1, name: 'Acme S.r.l.' })).toBeInTheDocument()
    expect(screen.getByText(REQUEST.descrizione)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Riassunto' })).toHaveValue(REQUEST.riassunto)
    expect(screen.getByRole('button', { name: 'Salva il riassunto' })).toBeInTheDocument()
    expect(screen.getByText('In sede, a Milano')).toBeInTheDocument()
    expect(screen.getByText('Pubblico')).toBeInTheDocument()

    const contatti = screen.getByRole('region', { name: 'Contatti' })
    expect(within(contatti).getByRole('link', { name: 'anna@acme.it' })).toHaveAttribute('href', 'mailto:anna@acme.it')
    expect(within(contatti).getByRole('link', { name: '+39 02 1234567' })).toHaveAttribute('href', 'tel:+39021234567')

    const stato = screen.getByRole('region', { name: 'Stato' })
    expect(within(stato).getByText('Nuova')).toBeInTheDocument()
    expect(within(stato).getByRole('button', { name: 'Segna come contattata' })).toBeInTheDocument()
    expect(within(stato).getByRole('button', { name: 'Chiudi' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Nota' })).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Salva la nota' })).toBeInTheDocument()

    // A talent already has the availability mail (D1): writing again is to the silent.
    expect(screen.getByRole('button', { name: 'Rimanda a chi non ha risposto' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Contatta i talenti' })).toBeNull()
  })

  it('shows the team by name, each linking to the talent, with the role, the rate, the band and the answer', async () => {
    serve()
    mount()

    const team = await screen.findByRole('region', { name: 'Il team' })
    const ada = within(team).getByRole('link', { name: 'Ada Lovelace' })
    expect(ada.getAttribute('href')).toMatch(/\/admin\/freelance\/f1$/)
    const adaRow = ada.closest('tr')!
    expect(cellUnder(adaRow, 'Ruolo')).toHaveTextContent('Backend developer')
    expect(cellUnder(adaRow, 'Ruolo')).toHaveTextContent('Ha scritto API di pagamento per tre anni.')
    expect(cellUnder(adaRow, 'Tariffa')).toHaveTextContent('450,00 €')
    expect(cellUnder(adaRow, 'Fascia cliente')).toHaveTextContent(`500${DASH}650 € al giorno`)
    expect(cellUnder(adaRow, 'Risposta')).toHaveTextContent(/^—$/)

    const graceRow = within(team).getByRole('link', { name: 'Grace Hopper' }).closest('tr')!
    expect(cellUnder(graceRow, 'Tariffa')).toHaveTextContent(/^—$/)
    expect(cellUnder(graceRow, 'Fascia cliente')).toHaveTextContent(/^—$/)
    expect(cellUnder(graceRow, 'Risposta')).toHaveTextContent(/^Sì/)
    expect(cellUnder(graceRow, 'Risposta')).toHaveTextContent('26 set 2026')
    // A member without a rate leaves the team with no band.
    expect(screen.getByText('Tariffa da definire')).toBeInTheDocument()
  })

  it('saves the summary with «Salva il riassunto»', async () => {
    const edited = 'Una web app per i clienti di una società di pagamenti.'
    const spy = serve((method, url, body) => {
      expect(`${method} ${url}`).toBe('PATCH /api/hub/team/requests/r1/summary')
      expect(body).toEqual({ riassunto: edited })
      return answer(200, { ...REQUEST, riassunto: edited, proposal: { ...REQUEST.proposal, riassunto: edited } })
    })
    mount()

    const box = await screen.findByRole('textbox', { name: 'Riassunto' })
    await userEvent.clear(box)
    await userEvent.type(box, edited)
    await userEvent.click(screen.getByRole('button', { name: 'Salva il riassunto' }))

    expect(await screen.findByText('Riassunto salvato.')).toBeInTheDocument()
    expect(box).toHaveValue(edited)
    expect(spy).toHaveBeenCalledWith(
      '/api/hub/team/requests/r1/summary',
      expect.objectContaining({ method: 'PATCH' }),
    )
  })

  it('shows the API’s refusal of a summary as it stands, and keeps what the admin typed', async () => {
    // `team_requests.NAMES_THE_COMPANY`, a 409.
    const refusal = "Il riassunto nomina l'azienda: correggilo prima di scrivere ai talenti."
    serve(() => answer(409, { detail: refusal }))
    mount()

    const box = await screen.findByRole('textbox', { name: 'Riassunto' })
    await userEvent.clear(box)
    await userEvent.type(box, 'Acme rifà il gestionale.')
    await userEvent.click(screen.getByRole('button', { name: 'Salva il riassunto' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(refusal)
    expect(box).toHaveValue('Acme rifà il gestionale.')
  })

  it('does not send an empty summary', async () => {
    const spy = serve()
    mount()

    await userEvent.clear(await screen.findByRole('textbox', { name: 'Riassunto' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva il riassunto' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Il riassunto non può essere vuoto.')
    expect(spy.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true)
  })

  it('saves the note with «Salva la nota», and clears it with an empty box', async () => {
    const bodies: unknown[] = []
    serve((method, url, body) => {
      expect(`${method} ${url}`).toBe('PATCH /api/hub/team/requests/r1/note')
      bodies.push(body)
      const note = (body as { note: string | null }).note
      return answer(200, { ...REQUEST, note })
    })
    mount()

    const box = await screen.findByRole('textbox', { name: 'Nota' })
    await userEvent.type(box, 'Richiamare lunedì.')
    await userEvent.click(screen.getByRole('button', { name: 'Salva la nota' }))
    expect(await screen.findByText('Nota salvata.')).toBeInTheDocument()

    await userEvent.clear(box)
    await userEvent.click(screen.getByRole('button', { name: 'Salva la nota' }))
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies).toEqual([{ note: 'Richiamare lunedì.' }, { note: null }])
  })

  it('marks the request contacted, closes it, and reopens it after a mis-click', async () => {
    const posted: unknown[] = []
    serve((method, url, body) => {
      expect(`${method} ${url}`).toBe('POST /api/hub/team/requests/r1/status')
      posted.push(body)
      const stato = (body as { stato: string }).stato
      return answer(200, {
        ...REQUEST,
        stato,
        contacted_at: '2026-09-26T08:00:00Z',
        closed_at: stato === 'chiusa' ? '2026-09-26T09:00:00Z' : null,
      })
    })
    mount()

    const stato = await screen.findByRole('region', { name: 'Stato' })
    await userEvent.click(within(stato).getByRole('button', { name: 'Segna come contattata' }))
    await within(stato).findByText('Contattata')
    expect(within(stato).queryByRole('button', { name: 'Segna come contattata' })).toBeNull()
    expect(within(stato).getByText(/Contattata il 26 set 2026/)).toBeInTheDocument()

    await userEvent.click(within(stato).getByRole('button', { name: 'Chiudi' }))
    await within(stato).findByText('Chiusa')
    expect(within(stato).queryByRole('button', { name: 'Chiudi' })).toBeNull()
    expect(within(stato).queryByRole('button', { name: 'Segna come contattata' })).toBeNull()
    expect(within(stato).getByText(/Chiusa il 26 set 2026/)).toBeInTheDocument()

    // Reopened, a request already contacted goes back to «Contattata», not to «Nuova».
    await userEvent.click(within(stato).getByRole('button', { name: 'Riapri' }))
    await within(stato).findByText('Contattata')
    expect(posted).toEqual([{ stato: 'contattata' }, { stato: 'chiusa' }, { stato: 'contattata' }])
  })

  it('shows the API’s sentence when a state change is refused', async () => {
    serve(() => answer(404, { detail: 'Richiesta team non trovata.' }))
    mount()
    const stato = await screen.findByRole('region', { name: 'Stato' })
    await userEvent.click(within(stato).getByRole('button', { name: 'Chiudi' }))
    expect(await within(stato).findByRole('alert')).toHaveTextContent('Richiesta team non trovata.')
  })

  it('has no summary to edit for a request of one talent from the cloud', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        ...REQUEST,
        proposal: null,
        riassunto: null,
        descrizione: null,
        origine: 'cloud',
        talenti: [REQUEST.talenti[0]],
      }),
    )
    mount()
    await screen.findByRole('heading', { level: 1, name: 'Acme S.r.l.' })
    expect(screen.queryByRole('textbox', { name: 'Riassunto' })).toBeNull()
    expect(screen.getByText('Una richiesta per un talento solo: non c’è un riassunto.')).toBeInTheDocument()
    expect(screen.getByText('Cloud')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Ada Lovelace' })).toBeInTheDocument()
  })

  it('says so when the request does not exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(404, { detail: 'Richiesta team non trovata.' }))
    mount()
    expect(await screen.findByText('Richiesta non trovata.')).toBeInTheDocument()
  })
})

/** `REQUEST` before anyone was mailed: every talent silent, no answer, no send. */
const FRESH = {
  ...REQUEST,
  talenti: REQUEST.talenti.map((talent) => ({ ...talent, mail_sent_at: null, risposta: null, risposta_at: null })),
}

/** Answers the page's read with `request`, and hands every write to `write`. */
function serveRequest(request: unknown, write: (method: string, url: string) => Response) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (method === 'GET' && url === '/api/hub/team/requests/r1') return answer(200, request)
    if (method === 'GET') throw new Error(`unhandled fetch in this test: ${method} ${url}`)
    return write(method, url)
  })
}

describe('the talents’ availability on the request’s page (REB-517, spec § 3.5, § 3.6)', () => {
  it('contacts every talent the first time, then offers to write again to the silent', async () => {
    const calls: string[] = []
    serveRequest(FRESH, (method, url) => {
      calls.push(`${method} ${url}`)
      return answer(200, {
        ...FRESH,
        stato: 'contattata',
        contacted_at: '2026-09-26T08:00:00Z',
        talenti: FRESH.talenti.map((talent) => ({ ...talent, mail_sent_at: '2026-09-26T08:00:00Z' })),
      })
    })
    mount()

    const team = await screen.findByRole('region', { name: 'Il team' })
    expect(within(team).queryByRole('button', { name: 'Rimanda a chi non ha risposto' })).toBeNull()
    for (const link of within(team).getAllByRole('link')) {
      expect(cellUnder(link.closest('tr')!, 'Risposta')).toHaveTextContent(/^—$/)
    }
    await userEvent.click(within(team).getByRole('button', { name: 'Contatta i talenti' }))

    expect(await within(team).findByRole('button', { name: 'Rimanda a chi non ha risposto' })).toBeInTheDocument()
    expect(calls).toEqual(['POST /api/hub/team/requests/r1/contact?only_silent=false'])
    expect(within(team).getByRole('status')).toHaveTextContent('Mail in partenza')
    for (const link of within(team).getAllByRole('link')) {
      expect(cellUnder(link.closest('tr')!, 'Risposta')).toHaveTextContent(/^In attesa$/)
    }
    expect(within(screen.getByRole('region', { name: 'Stato' })).getByText('Contattata')).toBeInTheDocument()
  })

  it('writes again only to the silent with «Rimanda a chi non ha risposto»', async () => {
    const calls: string[] = []
    serveRequest(REQUEST, (method, url) => {
      calls.push(`${method} ${url}`)
      return answer(200, REQUEST)
    })
    mount()

    const team = await screen.findByRole('region', { name: 'Il team' })
    await userEvent.click(within(team).getByRole('button', { name: 'Rimanda a chi non ha risposto' }))

    expect(await within(team).findByRole('status')).toHaveTextContent('Mail in partenza')
    expect(calls).toEqual(['POST /api/hub/team/requests/r1/contact?only_silent=true'])
  })

  it('shows the answers with their time, «In attesa» for the silent and «—» for who was never mailed', async () => {
    serveRequest(
      {
        ...REQUEST,
        talenti: [
          { ...REQUEST.talenti[0], mail_sent_at: '2026-09-25T12:00:00Z', risposta: 'no', risposta_at: '2026-09-27T16:45:00Z' },
          { ...REQUEST.talenti[1], risposta: null, risposta_at: null },
        ],
      },
      () => answer(500, {}),
    )
    mount()

    const team = await screen.findByRole('region', { name: 'Il team' })
    const ada = cellUnder(within(team).getByRole('link', { name: 'Ada Lovelace' }).closest('tr')!, 'Risposta')
    expect(ada).toHaveTextContent(/^No · /)
    expect(ada).toHaveTextContent('27 set 2026')
    const grace = cellUnder(within(team).getByRole('link', { name: 'Grace Hopper' }).closest('tr')!, 'Risposta')
    expect(grace).toHaveTextContent(/^In attesa$/)
  })

  it('shows the refusal of a summary that names the company, and keeps the button', async () => {
    const refusal = "Il riassunto nomina l'azienda: correggilo prima di scrivere ai talenti."
    serveRequest(FRESH, () => answer(409, { detail: refusal }))
    mount()

    const team = await screen.findByRole('region', { name: 'Il team' })
    await userEvent.click(within(team).getByRole('button', { name: 'Contatta i talenti' }))

    expect(await within(team).findByRole('alert')).toHaveTextContent(refusal)
    expect(within(team).getByRole('button', { name: 'Contatta i talenti' })).toBeEnabled()
  })

  it('offers nothing to send once everyone answered', async () => {
    const answered = {
      ...REQUEST,
      talenti: REQUEST.talenti.map((talent) => ({
        ...talent,
        mail_sent_at: '2026-09-25T12:00:00Z',
        risposta: 'si',
        risposta_at: '2026-09-26T09:30:00Z',
      })),
    }
    serveRequest(answered, () => answer(500, {}))
    mount()
    const team = await screen.findByRole('region', { name: 'Il team' })
    expect(within(team).queryByRole('button')).toBeNull()
  })

  it('offers nothing to send on a closed request', async () => {
    serveRequest({ ...FRESH, stato: 'chiusa', closed_at: '2026-09-26T09:00:00Z' }, () => answer(500, {}))
    mount()
    const team = await screen.findByRole('region', { name: 'Il team' })
    expect(within(team).queryByRole('button')).toBeNull()
  })
})

