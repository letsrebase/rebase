import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import { expect, vi } from 'vitest'
import type { CompaniesFilters, Remoto, TalentiFilters } from '@/lib/api'
import { parseSearch } from '@/lib/search'
import { strParam } from '@/router'
import { AdminCompanies } from './companies/CompanyList'
import { AdminCompanyDetail } from './companies/CompanyDetail'
import { AdminFreelancerDetail } from './freelancer/FreelancerDetail'
import { AdminTalenti } from './talent/TalentList'
import { AdminTalentoLead } from './talent/LeadForm'

/** What the Talenti and Aziende page tests share: a mocked `fetch` routed by endpoint,
 *  the fixtures every one of them reads, and `mount`, which puts the admin routes in a
 *  memory router. */
export function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Routes a mocked `fetch` by exact `"<method> <url>"`, so a page reading more than
 *  one endpoint on mount (REB-355's own audit trail, fetched alongside the record
 *  itself) gets the right shape for each rather than one blanket response replayed at
 *  both -- a single `Response` cannot even be read twice, which is what silently broke
 *  the freelancer/company detail page the moment `AuditTrail` added its own fetch. */
export function routeFetch(handlers: Record<string, unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    if (!(key in handlers)) throw new Error(`unhandled fetch in this test: ${key}`)
    return answer(200, handlers[key])
  })
}

/** A card the admin wrote from a signup (ORB-155): no CV, no rate, no position, no
 *  remote preference, and the attribution that says so. */
export const INCOMPLETE = {
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
  utm_source: 'linkedin',
  utm_campaign: null,
  created_at: '2026-09-10T10:00:00Z',
  compilata_da: 'admin',
  completa: false,
  note: [],
  accessi: 0,
  provenienza: 'form',
  ultimo_accesso: null,
  iscrizione_utm: null,
  ultimi_accessi: [],
  ultimi_download_guida: [],
  pigro_slug: null,
  deleted_at: null,
}

export const COMPLETE = {
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

/** `GET /api/hub/freelancers/{id}/card` before any card is written (REB-514): the
 *  detail page's «Scheda anonima» reads it beside the card itself. */
export function noCard(id: string) {
  return {
    freelancer_id: id,
    card: null,
    modalita: null,
    fascia: null,
    cv_sha256: null,
    model: null,
    generated_at: null,
    error: null,
  }
}

/** A card in `talenti` (REB-282/283): a freelancer already written, `origine` naming
 *  the wizard the person filled in themselves. */
export const CARD_TALENTO = {
  id: 'f1',
  nome: 'Ada',
  cognome: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  stato: 'nuovo',
  origine: 'wizard',
  utm_source: 'linkedin',
  created_at: '2026-09-10T10:00:00Z',
  // REB-558: a card's own day rate and work mode.
  tariffa_giornaliera: '450.00',
  remoto: 'remoto',
  // REB-800: the other live cards that may be the same person; empty for almost everyone.
  possibile_duplicato_di: [],
}

/** A bare sign-up in `talenti` (ORB-163): `stato` `lead`, no card behind it yet, so
 *  no rate and no mode (REB-558). */
export const LEAD_TALENTO = {
  id: 's2',
  nome: 'Bob',
  cognome: 'Ross',
  email: 'bob@example.org',
  linkedin_url: 'https://www.linkedin.com/in/bob',
  stato: 'lead',
  origine: 'form',
  utm_source: 'newsletter',
  created_at: '2026-09-08T10:00:00Z',
  tariffa_giornaliera: null,
  remoto: null,
  possibile_duplicato_di: [],
}

/** A company request in `GET /api/hub/companies` (REB-286's own new coverage: the
 *  list had none before). */
export const COMPANY_A = {
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
  origine: 'home',
  utm_source: 'linkedin',
  created_at: '2026-09-10T10:00:00Z',
  note: [],
  deleted_at: null,
}

export const COMPANY_B = { ...COMPANY_A, id: 'c2', nome_azienda: 'Bianchi Srl', stato: 'contattato' }

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
export function settle(): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>()
  setTimeout(resolve, 500)
  return promise
}

/** `GET /api/hub/talent/duplicati` when nobody is on the list twice. */
export const NO_DUPLICATES = { unibili: [], da_rivedere: [], iscrizioni: 0 }

/** The admin routes these pages sit on, without the frame and its guard: the pages
 *  read `useParams` and render `Link`s, so a router has to be there. The pathless
 *  `signedIn` id mirrors the real tree (REB-279's `SignedInLayout`), since
 *  `AdminFreelancerDetail`'s and `AdminTalentoLead`'s own `useParams({ from })` name
 *  that full route id. `talent`/`companies` carry the same `validateSearch` shape
 *  `router.tsx` gives them (REB-286), duplicated rather than imported the same way
 *  `Thanks.test.tsx` duplicates `thanks`'s own. Returns the router so a test can read
 *  `router.state.location.search` back out after an interaction. */
export function mount(path: string, duplicates: Record<string, unknown> | (() => Response) = NO_DUPLICATES) {
  // REB-800: Talenti and a card's page read the duplicate pairs too, beside what the
  // test mocked for the page itself. They are answered here, apart, so a test that hands
  // one `Response` to every call (which can be read once) and its call counts stay as
  // they were; a test about the pairs passes its own body, or a function making the
  // whole `Response` when the answer itself is the point.
  const inner = globalThis.fetch
  vi.stubGlobal('fetch', (input: Parameters<typeof fetch>[0], init?: RequestInit) =>
    String(input) === '/api/hub/talent/duplicati'
      ? Promise.resolve(typeof duplicates === 'function' ? duplicates() : answer(200, duplicates))
      : inner(input, init),
  )
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
      pagina_origine: strParam(search.pagina_origine),
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
  const duplicati = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/talent/duplicates',
    component: () => <p>duplicati</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([
      signedIn.addChildren([talent, talentLead, freelanceDetail, contratti, nuovoMatch, companies, companiesDetail, duplicati]),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
    parseSearch,
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
export function cellUnder(row: HTMLElement, header: string): HTMLElement {
  const table = row.closest('table')!
  const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
  const index = headers.indexOf(header)
  expect(index).toBeGreaterThanOrEqual(0)
  return within(row).getAllByRole('cell')[index]!
}

export const CV_PERSONA = {
  nome: 'Grace',
  cognome: 'Hopper',
  email: 'grace.cv@example.org',
  telefono: '+39 333 123 4567',
  citta: 'Torino',
  linkedin_url: 'https://www.linkedin.com/in/grace-cv',
  altri_link: ['https://github.com/grace-cv', 'https://github.com/ada'],
  aziende: ['Acme Pagamenti', 'Bottega Digitale'],
}

/** The value cell of a profile row, by its label. */
export function valueOf(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: 'dt' })
  return term.nextElementSibling as HTMLElement
}

export async function mountDetail(profile: Record<string, unknown>, card: Record<string, unknown>) {
  routeFetch({
    'GET /api/hub/freelancers/f2': { ...COMPLETE, ...profile },
    'GET /api/hub/freelancers/f2/audit': [],
    'GET /api/hub/freelancers/f2/card': { ...noCard('f2'), ...card },
  })
  mount('/admin/freelance/f2')
  await screen.findByRole('heading', { name: 'Grace Hopper' })
}

/** A card an admin marked «Verificato» (REB-518). */
export const VETTED_TALENTO = { ...CARD_TALENTO, vetted_at: '2026-09-25T09:00:00Z', ha_scheda_anonima: true }

/** A company request that came from the team builder's beta box (spec § 4.3). */
export const COMPANY_TEAM_BUILDER = { ...COMPANY_A, origine: 'team-builder', talent_cloud_grant: null }

/** The live grant `POST /api/hub/companies/c1/cloud` answers (REB-518). */
export const GRANT = {
  id: 'g1',
  user_id: 'u1',
  company_id: 'c1',
  azienda: 'Rossi Studio',
  referente: 'Mario Rossi',
  email: 'mario@rossi.it',
  granted_by: 'a1',
  granted_by_nome: 'Ivan',
  granted_at: '2026-09-26T10:00:00Z',
  revoked_by: null,
  revoked_by_nome: null,
  revoked_at: null,
}

/** Two live cards of one person (REB-800), as `GET /api/hub/talent/duplicati` reads them. */
export function person(id: string, nome: string, cognome: string) {
  return {
    freelancer_id: id,
    user_id: `u-${id}`,
    nome,
    cognome,
    email: `${nome.toLowerCase()}@studio.it`,
    created_at: '2026-09-10T10:00:00Z',
    cv_uploaded_at: null,
    ha_cv: false,
  }
}
export const LINKED_PAIR = {
  survivor: person('f2', 'Grace', 'Hopper'),
  assorbito: person('f1', 'Ada', 'Lovelace'),
  segnali: ['linkedin'],
  unibile: true,
}
export const NAME_PAIR = { ...LINKED_PAIR, survivor: person('f3', 'Alan', 'Turing'), segnali: ['nome'], unibile: false }

// The stub goes first: it was installed over the test's own spy, and putting back the
// spy's value before the spy is restored would leave it in `fetch` for the next test.
export function resetMocks() {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
}
