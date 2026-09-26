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
import type { CompaniesFilters, Remoto, TalentiFilters } from '@/lib/api'
import { strParam } from '@/router'
import { AdminCompanies, AdminCompanyDetail, AdminFreelancerDetail, AdminTalenti, AdminTalentoLead } from './lists'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Routes a mocked `fetch` by exact `"<method> <url>"`, so a page reading more than
 *  one endpoint on mount (REB-355's own audit trail, fetched alongside the record
 *  itself) gets the right shape for each rather than one blanket response replayed at
 *  both -- a single `Response` cannot even be read twice, which is what silently broke
 *  the freelancer/company detail page the moment `AuditTrail` added its own fetch. */
function routeFetch(handlers: Record<string, unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    if (!(key in handlers)) throw new Error(`unhandled fetch in this test: ${key}`)
    return answer(200, handlers[key])
  })
}

/** A card the admin wrote from a signup (ORB-155): no CV, no rate, no position, no
 *  remote preference, and the attribution that says so. */
const INCOMPLETE = {
  id: 'f1',
  nome: 'Ada',
  cognome: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  cv_filename: null,
  cv_size: null,
  tariffa_giornaliera: null,
  posizione: null,
  remoto: null,
  links: ['https://github.com/ada'],
  stato: 'nuovo',
  note: null,
  utm_source: 'linkedin',
  utm_campaign: null,
  created_at: '2026-09-10T10:00:00Z',
  compilata_da: 'admin',
  completa: false,
  commenti: [],
  accessi: 0,
  provenienza: 'form',
  ultimo_accesso: null,
  iscrizione_utm: null,
  ultimi_accessi: [],
  ultimi_download_guida: [],
  pigro_slug: null,
  deleted_at: null,
}

const COMPLETE = {
  ...INCOMPLETE,
  id: 'f2',
  nome: 'Grace',
  cognome: 'Hopper',
  email: 'grace@studio.it',
  cv_filename: 'Grace CV.pdf',
  cv_size: 2048,
  tariffa_giornaliera: '500.00',
  posizione: 'CTO',
  remoto: 'remoto',
  compilata_da: 'persona',
  completa: true,
  accessi: 3,
  provenienza: 'landing',
  ultimo_accesso: '2026-09-11T12:04:00Z',
}

/** A card in `talenti` (REB-282/283): a freelancer already written, `origine` naming
 *  the wizard the person filled in themselves. */
const CARD_TALENTO = {
  id: 'f1',
  nome: 'Ada',
  cognome: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  stato: 'nuovo',
  origine: 'wizard',
  utm_source: 'linkedin',
  created_at: '2026-09-10T10:00:00Z',
}

/** A bare sign-up in `talenti` (ORB-163): `stato` `lead`, no card behind it yet. */
const LEAD_TALENTO = {
  id: 's2',
  nome: 'Bob',
  cognome: 'Ross',
  email: 'bob@example.org',
  linkedin_url: 'https://www.linkedin.com/in/bob',
  stato: 'lead',
  origine: 'form',
  utm_source: 'newsletter',
  created_at: '2026-09-08T10:00:00Z',
}

/** A company request in `GET /api/hub/companies` (REB-286's own new coverage: the
 *  list had none before). */
const COMPANY_A = {
  id: 'c1',
  nome_azienda: 'Rossi Studio',
  referente: 'Mario Rossi',
  email: 'mario@rossi.it',
  telefono: '+39 345 1234567',
  figura_richiesta: 'Backend developer',
  progetto: 'Piattaforma di prenotazione',
  periodo_da: '2026-10-01',
  durata: '3 mesi',
  budget_giornaliero: '450.00',
  remoto: 'ibrido',
  giorni_presenza: 3,
  numero_risorse: 2,
  stato: 'nuovo',
  note: null,
  origine: 'home',
  utm_source: 'linkedin',
  created_at: '2026-09-10T10:00:00Z',
  commenti: [],
  deleted_at: null,
}

const COMPANY_B = { ...COMPANY_A, id: 'c2', nome_azienda: 'Bianchi Srl', stato: 'contattato' }

// The project's `lib` target is ES2022 and does not declare `Promise.withResolvers`
// (Node 22 and Vitest's own runtime both support it regardless): this augments the
// ambient type for this file alone, rather than raising the shared `tsconfig.json`'s
// `lib` for the whole app over one helper.
declare global {
  interface PromiseConstructor {
    withResolvers<T>(): {
      promise: Promise<T>
      resolve: (value: T | PromiseLike<T>) => void
      reject: (reason?: unknown) => void
    }
  }
}

/** The 300ms debounce plus a margin, in real time -- real timers, not fake ones, so
 *  `userEvent`'s own awaiting and the debounce never deadlock each other (same
 *  reasoning as PigroCRM's `CommandPalette.test.tsx`). */
function settle(): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>()
  setTimeout(resolve, 500)
  return promise
}

/** The admin routes these pages sit on, without the frame and its guard: the pages
 *  read `useParams` and render `Link`s, so a router has to be there. The pathless
 *  `signedIn` id mirrors the real tree (REB-279's `SignedInLayout`), since
 *  `AdminFreelancerDetail`'s and `AdminTalentoLead`'s own `useParams({ from })` name
 *  that full route id. `talent`/`companies` carry the same `validateSearch` shape
 *  `router.tsx` gives them (REB-286), duplicated rather than imported the same way
 *  `Thanks.test.tsx` duplicates `thanks`'s own. Returns the router so a test can read
 *  `router.state.location.search` back out after an interaction. */
function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const talent = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/talent',
    component: AdminTalenti,
    validateSearch: (search: Record<string, unknown>): TalentiFilters => ({
      stato: strParam(search.stato),
      q: strParam(search.q),
      posizione: strParam(search.posizione),
      remoto:
        search.remoto === 'remoto' || search.remoto === 'ibrido' || search.remoto === 'in_sede'
          ? (search.remoto as Remoto)
          : undefined,
      tariffa_min: strParam(search.tariffa_min),
      tariffa_max: strParam(search.tariffa_max),
      origine: strParam(search.origine),
      utm_source: strParam(search.utm_source),
      has_cv: search.has_cv === true || search.has_cv === 'true' ? true : search.has_cv === false || search.has_cv === 'false' ? false : undefined,
      con_accessi:
        search.con_accessi === true || search.con_accessi === 'true'
          ? true
          : search.con_accessi === false || search.con_accessi === 'false'
            ? false
            : undefined,
      creato_da: strParam(search.creato_da),
      creato_a: strParam(search.creato_a),
    }),
  })
  const talentLead = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/talent/$id',
    component: AdminTalentoLead,
  })
  const freelanceDetail = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id',
    component: AdminFreelancerDetail,
  })
  const contratti = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: () => <p>contratti</p>,
  })
  const nuovoMatch = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/match/new',
    component: () => <p>nuovo match</p>,
  })
  const companies = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/companies',
    component: AdminCompanies,
    validateSearch: (search: Record<string, unknown>): CompaniesFilters => ({
      stato: strParam(search.stato),
      q: strParam(search.q),
      budget_min: strParam(search.budget_min),
      budget_max: strParam(search.budget_max),
      periodo_da: strParam(search.periodo_da),
      origine: strParam(search.origine),
      creato_da: strParam(search.creato_da),
      creato_a: strParam(search.creato_a),
    }),
  })
  const companiesDetail = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/companies/$id',
    component: AdminCompanyDetail,
  })
  const router = createRouter({
    routeTree: root.addChildren([
      signedIn.addChildren([talent, talentLead, freelanceDetail, contratti, nuovoMatch, companies, companiesDetail]),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

/** The cell under a given column header, so a «—» is checked where it is expected and
 *  not anywhere on the row. */
function cellUnder(row: HTMLElement, header: string): HTMLElement {
  const table = row.closest('table')!
  const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
  const index = headers.indexOf(header)
  expect(index).toBeGreaterThanOrEqual(0)
  return within(row).getAllByRole('cell')[index]!
}

afterEach(() => vi.restoreAllMocks())

describe('the Talenti list (REB-282/283)', () => {
  it('lists a card and a lead together, each with its own state and origin, and links to the right page', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 2, items: [CARD_TALENTO, LEAD_TALENTO], per_stato: { nuovo: 1, lead: 1 } }),
    )
    mount('/admin/talent')

    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(cellUnder(ada, 'Stato')).getByText('Nuovo')).toBeInTheDocument()
    expect(cellUnder(ada, 'Provenienza')).toHaveTextContent('wizard')
    const adaLink = within(ada).getByRole('link')
    expect(adaLink.getAttribute('href')).toMatch(/\/admin\/freelance\/f1$/)

    const bob = screen.getByText('bob@example.org').closest('tr')!
    expect(within(cellUnder(bob, 'Stato')).getByText('Lead')).toBeInTheDocument()
    expect(cellUnder(bob, 'Provenienza')).toHaveTextContent('form')
    const bobLink = within(bob).getByRole('link')
    expect(bobLink.getAttribute('href')).toMatch(/\/admin\/talent\/s2$/)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('2')
  })

  it('filters by state through the same pills as before, «Lead» included', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })
    expect(screen.getByRole('button', { name: 'Nuovo' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Lead' })).toBeInTheDocument()
  })
})

describe('a lead offers to draft a card in place (ORB-155, REB-283)', () => {
  it('shows what the sign-up says, drafts a card from the given sources, and opens the new card', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (init?.method === 'POST') {
        expect(url).toBe('/api/hub/signups/s2/card')
        expect(JSON.parse(init.body as string)).toEqual({
          nome: 'Bob',
          cognome: 'Ross',
          linkedin_url: 'https://www.linkedin.com/in/bob',
          links: [],
          fonti: ['https://bob.dev'],
        })
        return answer(201, { ...INCOMPLETE, id: 'f9', nome: 'Bob', cognome: 'Ross' })
      }
      if (url === '/api/hub/freelancers/f9') {
        return answer(200, { ...INCOMPLETE, id: 'f9', nome: 'Bob', cognome: 'Ross' })
      }
      if (url === '/api/hub/freelancers/f9/audit') {
        return answer(200, [])
      }
      return answer(200, { totale: 1, items: [LEAD_TALENTO], per_stato: { lead: 1 } })
    })
    mount('/admin/talent/s2')

    await screen.findByRole('heading', { name: 'Bob Ross' })
    expect(screen.getByDisplayValue('Bob')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Ross')).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Fonti'), 'https://bob.dev')
    await userEvent.click(screen.getByRole('button', { name: 'Crea scheda' }))

    // Landing on the existing freelancer detail (not rewritten here, REB-284's job):
    // its own ownership sentence for a card an admin wrote is proof the redirect worked,
    // and the GET above proves the redirect's $id is the card the POST actually created.
    expect(await screen.findByText('scritta dall’admin, da completare')).toBeInTheDocument()
    expect(spy).toHaveBeenCalled()
  })

  it('drafts a card with a rate typed as «1.500» as 1500 (REB-485)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (init?.method === 'POST') return answer(201, { ...INCOMPLETE, id: 'f9', nome: 'Bob', cognome: 'Ross' })
      if (url === '/api/hub/freelancers/f9') {
        return answer(200, { ...INCOMPLETE, id: 'f9', nome: 'Bob', cognome: 'Ross' })
      }
      if (url === '/api/hub/freelancers/f9/audit') return answer(200, [])
      return answer(200, { totale: 1, items: [LEAD_TALENTO], per_stato: { lead: 1 } })
    })
    mount('/admin/talent/s2')

    await screen.findByRole('heading', { name: 'Bob Ross' })
    await userEvent.type(screen.getByLabelText('Tariffa a giornata'), '1.500')
    await userEvent.type(screen.getByLabelText('Fonti'), 'https://bob.dev')
    await userEvent.click(screen.getByRole('button', { name: 'Crea scheda' }))

    await screen.findByText('scritta dall’admin, da completare')
    const post = spy.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(post[0]).toBe('/api/hub/signups/s2/card')
    expect(JSON.parse(post[1]!.body as string).tariffa_giornaliera).toBe('1500')
  })

  it('refuses without at least one source, since a card written from research needs one', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 1, items: [LEAD_TALENTO], per_stato: { lead: 1 } }),
    )
    mount('/admin/talent/s2')
    await screen.findByRole('heading', { name: 'Bob Ross' })
    expect(screen.getByLabelText('Fonti')).toBeRequired()
  })
})

describe('the freelancer detail', () => {
  it('shows an incomplete card without a CV link and says the admin wrote it', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': INCOMPLETE,
      'GET /api/hub/freelancers/f1/audit': [],
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    expect(spy).toHaveBeenCalledWith('/api/hub/freelancers/f1', expect.anything())
    expect(screen.queryByRole('link', { name: /CV/ })).toBeNull()
    expect(screen.getByText('Da completare')).toBeInTheDocument()
    expect(screen.getByText('scritta dall’admin, da completare')).toBeInTheDocument()
    expect(screen.getByText('Mai entrato')).toBeInTheDocument()
    // The three answers the person has not given yet read as dashes, not as a crash.
    const rows = screen.getAllByRole('definition')
    expect(rows.filter((row) => row.textContent === '—').length).toBeGreaterThanOrEqual(3)
  })

  it('shows a complete card with its CV and says the person filled it in', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.getByRole('link', { name: /CV/ })).toHaveAttribute('href', '/api/hub/freelancers/f2/cv')
    expect(screen.queryByText('Da completare')).toBeNull()
    expect(screen.getByText('compilata dalla persona')).toBeInTheDocument()
    expect(screen.getByText('Da remoto')).toBeInTheDocument()
    expect(screen.getByText(/^3 · ultimo 11 set 2026/)).toBeInTheDocument()
  })
})

describe('the enriched detail: sign-up, logins, downloads, Pigro space (REB-284)', () => {
  const ENRICHED = {
    ...COMPLETE,
    iscrizione_utm: {
      utm_source: 'newsletter',
      utm_medium: 'email',
      utm_campaign: 'autunno-2026',
      utm_content: null,
      utm_term: null,
      utm_id: null,
    },
    ultimi_accessi: [
      { id: 'l1', logged_at: '2026-09-12T09:00:00Z' },
      { id: 'l2', logged_at: '2026-09-11T09:00:00Z' },
    ],
    ultimi_download_guida: [{ id: 'd1', downloaded_at: '2026-09-10T09:00:00Z' }],
    pigro_slug: 'studio-grace',
  }

  it('shows the sign-up utm, the recent logins and downloads, and the Pigro slug, in order', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f2': ENRICHED, 'GET /api/hub/freelancers/f2/audit': [] })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual([
      'Iscrizione alla newsletter',
      'Ultimi accessi',
      'Download della guida',
      'Spazio PigroCRM',
      'Registro delle modifiche',
      'Commenti',
    ])
    expect(screen.getByText('newsletter')).toBeInTheDocument()
    expect(screen.getByText('email')).toBeInTheDocument()
    expect(screen.getByText('autunno-2026')).toBeInTheDocument()
    expect(screen.getByText('studio-grace')).toBeInTheDocument()
  })

  it('shows sensible empty values with none of the four sources, not a crash', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': {
        ...INCOMPLETE,
        iscrizione_utm: null,
        ultimi_accessi: [],
        ultimi_download_guida: [],
        pigro_slug: null,
      },
      'GET /api/hub/freelancers/f1/audit': [],
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    expect(screen.getByText('Nessuna iscrizione con questo indirizzo.')).toBeInTheDocument()
    expect(screen.getByText('Non è mai entrata.')).toBeInTheDocument()
    expect(screen.getByText('Non ha scaricato la guida.')).toBeInTheDocument()
    // No slug at all: the section does not render rather than showing an empty one.
    expect(screen.queryByText('Spazio PigroCRM')).toBeNull()
  })

  it('keeps the enriched sections after saving a state change from the plain PATCH response', async () => {
    // `PATCH /freelancers/{id}` answers a plain `FreelancerRead`: none of REB-284's
    // keys even exist on the body, since only `get`'s response model carries them.
    const plainCard: Record<string, unknown> = { ...ENRICHED, stato: 'contattato' }
    delete plainCard.iscrizione_utm
    delete plainCard.ultimi_accessi
    delete plainCard.ultimi_download_guida
    delete plainCard.pigro_slug
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f2/audit') return answer(200, [])
      if (init?.method === 'PATCH') return answer(200, plainCard)
      return answer(200, ENRICHED)
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.getByText('studio-grace')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const banner = await screen.findByRole('banner')
    await within(banner).findByText('Contattato')
    // The save must merge onto the cached detail, not replace it: the Pigro slug and
    // the other REB-284 sections the PATCH never answers stay on the page.
    expect(screen.getByText('studio-grace')).toBeInTheDocument()
    expect(screen.getByText('newsletter')).toBeInTheDocument()
  })
})

describe('REB-355: override, delete/restore and the audit trail on the freelancer detail', () => {
  it('overrides a field through "Modifica scheda" and shows it, keeping the existing comment thread', async () => {
    const withComment = {
      ...INCOMPLETE,
      commenti: [
        { id: 'c1', entity_type: 'freelancer', entity_id: 'f1', testo: 'Ha risposto alla call.', autore: 'Ivan', created_at: '2026-09-12T10:00:00Z' },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f1/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f1/override' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.posizione).toBe('Staff engineer')
        // The override response's own `commenti` is always `[]` (only `get` fills it):
        // a merge that took it verbatim would wipe the existing thread from view.
        return answer(200, { ...withComment, posizione: 'Staff engineer', commenti: [] })
      }
      return answer(200, withComment)
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    await screen.findByText('Ha risposto alla call.')
    await userEvent.click(screen.getByRole('button', { name: 'Modifica scheda' }))
    const posizione = await screen.findByLabelText('Posizione')
    await userEvent.type(posizione, 'Staff engineer')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByText('Staff engineer')).toBeInTheDocument())
    expect(screen.getByText('Ha risposto alla call.')).toBeInTheDocument()
  })

  it('deletes the card, hides the CV/edit actions, then restores it', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f2/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f2' && init?.method === 'DELETE') {
        return answer(200, { ...COMPLETE, deleted_at: '2026-09-23T10:00:00Z' })
      }
      if (url === '/api/hub/freelancers/f2/restore' && init?.method === 'POST') {
        return answer(200, { ...COMPLETE, deleted_at: null })
      }
      return answer(200, COMPLETE)
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.getByRole('button', { name: 'Modifica scheda' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Elimina' }))
    await screen.findByText(/Eliminata il/)
    expect(screen.queryByRole('button', { name: 'Modifica scheda' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Rimuovi CV' })).toBeNull()
    // `set_status` 404s on a deleted row (`FreelancerService._require`'s own default):
    // the note/state editor must not offer an action the backend will refuse.
    expect(screen.queryByRole('button', { name: 'Contattato' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    await waitFor(() => expect(screen.queryByText(/Eliminata il/)).toBeNull())
    expect(screen.getByRole('button', { name: 'Modifica scheda' })).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/freelancers/f2', expect.objectContaining({ method: 'DELETE' }))
  })

  it('clears the CV and drops its download link and button', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f2/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f2/cv' && init?.method === 'DELETE') {
        return answer(200, { ...COMPLETE, cv_filename: null, cv_size: null })
      }
      return answer(200, COMPLETE)
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi CV' }))
    await waitFor(() => expect(screen.queryByRole('link', { name: /CV/ })).toBeNull())
    expect(screen.queryByRole('button', { name: 'Rimuovi CV' })).toBeNull()
  })
})

describe('REB-356: the state-change mutations keep the comment thread visible', () => {
  it('keeps the existing comment thread after a state change on the freelancer detail', async () => {
    const withComment = {
      ...INCOMPLETE,
      commenti: [
        { id: 'c1', entity_type: 'freelancer', entity_id: 'f1', testo: 'Ha risposto alla call.', autore: 'Ivan', created_at: '2026-09-12T10:00:00Z' },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f1/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f1' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.stato).toBe('contattato')
        // The move response's own `commenti` is always `[]` (only `get` fills it):
        // a replace that took it verbatim would wipe the existing thread from view.
        return answer(200, { ...withComment, stato: 'contattato', commenti: [] })
      }
      return answer(200, withComment)
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    await screen.findByText('Ha risposto alla call.')

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const banner = await screen.findByRole('banner')
    await within(banner).findByText('Contattato')
    expect(screen.getByText('Ha risposto alla call.')).toBeInTheDocument()
  })

  it('keeps the existing comment thread after a state change on the company detail', async () => {
    const withComment = {
      ...COMPANY_A,
      commenti: [
        { id: 'c2', entity_type: 'company', entity_id: 'c1', testo: 'In attesa di risposta.', autore: 'Ivan', created_at: '2026-09-12T10:00:00Z' },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.stato).toBe('contattato')
        // The moveCompany response's own `commenti` is always `[]` (only `get` fills
        // it): the old `setQueryData(['company', id], updated)` replaced the cache
        // outright with that empty array, which is exactly what this guards against.
        return answer(200, { ...withComment, stato: 'contattato', commenti: [] })
      }
      return answer(200, withComment)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    await screen.findByText('In attesa di risposta.')

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const banner = await screen.findByRole('banner')
    await within(banner).findByText('Contattato')
    expect(screen.getByText('In attesa di risposta.')).toBeInTheDocument()
  })
})

describe('the company detail', () => {
  it('shows the request, its referente, its state and the REB-380 fields', async () => {
    routeFetch({
      'GET /api/hub/companies/c1': COMPANY_A,
      'GET /api/hub/companies/c1/audit': [],
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    expect(screen.getByText(/Mario Rossi/)).toBeInTheDocument()
    expect(screen.getByText('Piattaforma di prenotazione')).toBeInTheDocument()
    expect(screen.getByText('Backend developer')).toBeInTheDocument()
    expect(screen.getByText('Ibrido · 3 giorni a settimana in sede')).toBeInTheDocument()
    expect(screen.getByText(/\+39 345 1234567/)).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('overrides a field through "Modifica richiesta" and shows the new value', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1/override' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.durata).toBe('6 mesi')
        return answer(200, { ...COMPANY_A, durata: '6 mesi' })
      }
      return answer(200, COMPANY_A)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    await userEvent.click(screen.getByRole('button', { name: 'Modifica richiesta' }))
    const durata = await screen.findByLabelText('Durata')
    await userEvent.clear(durata)
    await userEvent.type(durata, '6 mesi')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByText(/6 mesi/)).toBeInTheDocument())
  })

  it('deletes the request, then restores it', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1' && init?.method === 'DELETE') {
        return answer(200, { ...COMPANY_A, deleted_at: '2026-09-23T10:00:00Z' })
      }
      if (url === '/api/hub/companies/c1/restore' && init?.method === 'POST') {
        return answer(200, { ...COMPANY_A, deleted_at: null })
      }
      return answer(200, COMPANY_A)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })

    await userEvent.click(screen.getByRole('button', { name: 'Elimina' }))
    await screen.findByText(/Eliminata il/)
    expect(screen.queryByRole('button', { name: 'Modifica richiesta' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    await waitFor(() => expect(screen.queryByText(/Eliminata il/)).toBeNull())
    expect(screen.getByRole('button', { name: 'Modifica richiesta' })).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/companies/c1', expect.objectContaining({ method: 'DELETE' }))
  })
})

describe('the search box debounces before it reaches the API and the URL (REB-286)', () => {
  it('waits for a pause in typing before searching Talenti', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 0, items: [], per_stato: {} }),
    )
    mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })
    spy.mockClear()

    await userEvent.type(screen.getByLabelText('Cerca'), 'ada')
    await settle()

    const calls = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/api/hub/talent'))
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('q=ada')
  })

  it('waits for a pause in typing before searching Aziende', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 0, items: [], per_stato: {} }),
    )
    mount('/admin/companies')
    await screen.findByRole('heading', { name: 'Aziende' })
    spy.mockClear()

    await userEvent.type(screen.getByLabelText('Cerca'), 'rossi')
    await settle()

    const calls = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/api/hub/companies'))
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('q=rossi')
  })
})

describe('every filter and the search box live in the URL, both ways (REB-286)', () => {
  it('reflects a state pill and a filter field into the address for Talenti', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    const router = mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })

    await userEvent.click(screen.getByRole('button', { name: 'Nuovo' }))
    await userEvent.type(screen.getByLabelText('Posizione'), 'CTO')

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ stato: 'nuovo', posizione: 'CTO' }),
    )
  })

  it('reads a filter and a state back out of a URL a link already carries, for Talenti', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/talent?posizione=CTO&stato=nuovo')
    await screen.findByRole('heading', { name: 'Talenti' })
    expect(screen.getByLabelText('Posizione')).toHaveValue('CTO')
  })

  it('keeps a purely numeric filter value on a fresh load, not just an in-app navigation', async () => {
    // The router's default parseSearch runs JSON.parse on every raw query value before
    // validateSearch sees it, so a digit-only value in the URL arrives as a JS number,
    // not a string -- exactly what happens opening a shared link or reloading, never
    // on an in-app navigate(). strParam has to coerce it back.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/talent?tariffa_min=50')
    await screen.findByRole('heading', { name: 'Talenti' })
    expect(screen.getByLabelText('Tariffa min (€/giorno)')).toHaveValue(50)
  })

  it('reflects a filter field into the address for Aziende', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    const router = mount('/admin/companies')
    await screen.findByRole('heading', { name: 'Aziende' })

    await userEvent.type(screen.getByLabelText('Pagina di provenienza'), 'home')

    await waitFor(() => expect(router.state.location.search).toMatchObject({ origine: 'home' }))
  })

  it('reads a filter back out of a URL a link already carries, for Aziende', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/companies?origine=pigrocrm')
    await screen.findByRole('heading', { name: 'Aziende' })
    expect(screen.getByLabelText('Pagina di provenienza')).toHaveValue('pigrocrm')
  })
})

describe('infinite scroll walks the cursor, a page at a time (REB-286)', () => {
  it('shows a second page of Talenti after «Mostra altri», cursor included in the request', async () => {
    const page1 = { totale: 2, items: [CARD_TALENTO], per_stato: { nuovo: 1 }, next_cursor: 'CURSOR1' }
    const page2 = { totale: 2, items: [LEAD_TALENTO], per_stato: { nuovo: 1, lead: 1 }, next_cursor: null }
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(200, url.searchParams.get('cursor') === 'CURSOR1' ? page2 : page1)
    })
    mount('/admin/talent')

    await screen.findByText('ada@studio.it')
    expect(screen.queryByText('bob@example.org')).toBeNull()
    expect(screen.getByText('Mostrati 1 talenti, ce ne sono altri.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))

    await screen.findByText('bob@example.org')
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(spy.mock.calls.some((call) => String(call[0]).includes('cursor=CURSOR1'))).toBe(true)
  })

  it('shows a second page of Aziende after «Mostra altri»', async () => {
    const page1 = { totale: 2, items: [COMPANY_A], per_stato: { nuovo: 1 }, next_cursor: 'CURSOR1' }
    const page2 = { totale: 2, items: [COMPANY_B], per_stato: { nuovo: 1, contattato: 1 }, next_cursor: null }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(200, url.searchParams.get('cursor') === 'CURSOR1' ? page2 : page1)
    })
    mount('/admin/companies')

    await screen.findByText('Rossi Studio')
    expect(screen.getByText('Mostrate 1 aziende, ce ne sono altre.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))

    await screen.findByText('Bianchi Srl')
    expect(screen.getByText('Rossi Studio')).toBeInTheDocument()
  })
})

describe('two empty states, in Italian (REB-286)', () => {
  it('says the Talenti table itself is empty with no filter active', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/talent')
    expect(await screen.findByText('Nessun profilo qui.')).toBeInTheDocument()
  })

  it('names the filters when one narrows Talenti to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/talent?posizione=Astrofisico')
    expect(await screen.findByText('Nessun risultato per questi filtri.')).toBeInTheDocument()
  })

  it('says the Aziende table itself is empty with no filter active', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/companies')
    expect(await screen.findByText('Nessuna richiesta qui.')).toBeInTheDocument()
  })

  it('names the filters when one narrows Aziende to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/companies?origine=home')
    expect(await screen.findByText('Nessun risultato per questi filtri.')).toBeInTheDocument()
  })
})

describe('the Aziende list renders a request (REB-286, previously untested)', () => {
  it('lists a request with its budget and period, and the total count', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 1, items: [COMPANY_A], per_stato: { nuovo: 1 } }),
    )
    mount('/admin/companies')
    expect(await screen.findByText('Rossi Studio')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('1')
  })
})

describe('the talent row menu and the card link to its contracts (REB-387)', () => {
  it('offers «Match e contratti» on a card row and no menu on a lead', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 2, items: [CARD_TALENTO, LEAD_TALENTO], per_stato: {} }),
    )
    mount('/admin/talent')
    const bob = (await screen.findByText('bob@example.org')).closest('tr')!
    expect(within(bob).queryByRole('button', { name: /Azioni per/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Azioni per Ada Lovelace' }))
    const item = await screen.findByRole('menuitem', { name: 'Match e contratti' })
    expect(item.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/contracts$/)
  })

  it('links a card to its matches and contracts from its header', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f2': COMPLETE, 'GET /api/hub/freelancers/f2/audit': [] })
    mount('/admin/freelance/f2')
    const link = await screen.findByRole('link', { name: 'Match e contratti' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f2\/contracts$/)
  })

  it('offers «Crea match» first in a card row menu', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 1, items: [CARD_TALENTO], per_stato: {} }))
    mount('/admin/talent')
    await userEvent.click(await screen.findByRole('button', { name: 'Azioni per Ada Lovelace' }))
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((item) => item.textContent)).toEqual(['Crea match', 'Match e contratti'])
    expect(items[0]!.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/match\/new$/)
  })
})
