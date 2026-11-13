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
import { parseSearch } from '@/lib/search'
import { validateCompaniesSearch, validateTalentsSearch } from '@/router'
import { AdminCompanies } from './companies/CompanyList'
import { AdminCompanyDetail } from './companies/CompanyDetail'
import { AdminFreelancerDetail } from './freelancer/FreelancerDetail'
import { AdminTalents } from './talent/TalentList'
import { AdminTalentLead } from './talent/LeadForm'

/** What the talent and company page tests share: a mocked `fetch` routed by endpoint,
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
  first_name: 'Ada',
  last_name: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  contact_points: [],
  cv_filename: null,
  cv_size: null,
  daily_rate: null,
  job_title: null,
  remote: null,
  links: ['https://github.com/ada'],
  status: 'new',
  utm_source: 'linkedin',
  utm_campaign: null,
  created_at: '2026-09-10T10:00:00Z',
  filled_by: 'admin',
  complete: false,
  note: [],
  logins: 0,
  provenance: 'form',
  last_access: null,
  signup_utm: null,
  last_logins: [],
  last_download_guide: [],
  pigro_slug: null,
  deleted_at: null,
}

export const COMPLETE = {
  ...INCOMPLETE,
  id: 'f2',
  first_name: 'Grace',
  last_name: 'Hopper',
  email: 'grace@studio.it',
  cv_filename: 'Grace CV.pdf',
  cv_size: 2048,
  daily_rate: '500.00',
  job_title: 'CTO',
  remote: 'remote',
  filled_by: 'person',
  complete: true,
  logins: 3,
  provenance: 'landing',
  last_access: '2026-09-11T12:04:00Z',
}

/** `GET /api/hub/freelancers/{id}/card` before any card is written (REB-514): the
 *  detail page's «Scheda anonima» reads it beside the card itself. */
export function noCard(id: string) {
  return {
    freelancer_id: id,
    card: null,
    mode: null,
    band: null,
    cv_sha256: null,
    model: null,
    generated_at: null,
    error: null,
  }
}

/** A card in `talents` (REB-282/283): a freelancer already written, `origin` naming
 *  the wizard the person filled in themselves. */
export const CARD_TALENT = {
  id: 'f1',
  first_name: 'Ada',
  last_name: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  status: 'new',
  origin: 'wizard',
  utm_source: 'linkedin',
  created_at: '2026-09-10T10:00:00Z',
  // REB-558: a card's own day rate and work mode.
  daily_rate: '450.00',
  remote: 'remote',
  // REB-823: what the anonymous card says; empty for a card without one and for a lead.
  role: null,
  seniority: null,
  experience_years: null,
  skills: [] as string[],
  location: null,
  // REB-800: the other live cards that may be the same person; empty for almost everyone.
  duplicate_possible_of: [],
}

/** A bare sign-up in `talents` (ORB-163): `status` `lead`, no card behind it yet, so
 *  no rate and no mode (REB-558). */
export const LEAD_TALENT = {
  id: 's2',
  first_name: 'Bob',
  last_name: 'Ross',
  email: 'bob@example.org',
  linkedin_url: 'https://www.linkedin.com/in/bob',
  status: 'lead',
  origin: 'form',
  utm_source: 'newsletter',
  created_at: '2026-09-08T10:00:00Z',
  daily_rate: null,
  remote: null,
  // REB-823: what the anonymous card says; empty for a card without one and for a lead.
  role: null,
  seniority: null,
  experience_years: null,
  skills: [] as string[],
  location: null,
  duplicate_possible_of: [],
}

/** A company request in `GET /api/hub/companies` (REB-286's own new coverage: the
 *  list had none before). */
export const COMPANY_A = {
  id: 'c1',
  company_name: 'Rossi Studio',
  contact_person: 'Mario Rossi',
  email: 'mario@rossi.it',
  phone: '+39 345 1234567',
  contact_points: [],
  requested_role: 'Backend developer',
  project: 'Piattaforma di prenotazione',
  period_from: '2026-10-01',
  duration: '3 mesi',
  daily_budget: '450.00',
  remote: 'hybrid',
  onsite_days: 3,
  headcount: 2,
  status: 'new',
  origin: 'home',
  utm_source: 'linkedin',
  created_at: '2026-09-10T10:00:00Z',
  note: [],
  deleted_at: null,
}

export const COMPANY_B = { ...COMPANY_A, id: 'c2', company_name: 'Bianchi Srl', status: 'contacted' }

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
export const NO_DUPLICATES = { mergeable: [], to_review: [], signups: 0 }

/** The admin routes these pages sit on, without the frame and its guard: the pages
 *  read `useParams` and render `Link`s, so a router has to be there. The pathless
 *  `signedIn` id mirrors the real tree (REB-279's `SignedInLayout`), since
 *  `AdminFreelancerDetail`'s and `AdminTalentLead`'s own `useParams({ from })` name
 *  that full route id. `talent`/`companies` take their `validateSearch` from `router.tsx`
 *  itself (REB-286, REB-983), so a new filter needs no change here. Returns the router so a test can read
 *  `router.state.location.search` back out after an interaction. */
export function mount(path: string, duplicates: Record<string, unknown> | (() => Response) = NO_DUPLICATES) {
  // REB-800: Talenti and a card's page read the duplicate pairs too, beside what the
  // test mocked for the page itself. They are answered here, apart, so a test that hands
  // one `Response` to every call (which can be read once) and its call counts stay as
  // they were; a test about the pairs passes its own body, or a function making the
  // whole `Response` when the answer itself is the point.
  const inner = globalThis.fetch
  vi.stubGlobal('fetch', (input: Parameters<typeof fetch>[0], init?: RequestInit) =>
    String(input) === '/api/hub/talent/duplicates'
      ? Promise.resolve(typeof duplicates === 'function' ? duplicates() : answer(200, duplicates))
      : inner(input, init),
  )
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const talent = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/talent',
    component: AdminTalents,
    validateSearch: validateTalentsSearch,
  })
  const talentLead = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/talent/$id',
    component: AdminTalentLead,
  })
  const freelanceDetail = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id',
    component: AdminFreelancerDetail,
  })
  const newMatch = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/match/new',
    component: () => <p>nuovo match</p>,
  })
  const companies = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/companies',
    component: AdminCompanies,
    validateSearch: validateCompaniesSearch,
  })
  const companiesDetail = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/companies/$id',
    component: AdminCompanyDetail,
  })
  const duplicatesRoute = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/talent/duplicates',
    component: () => <p>duplicati</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([
      signedIn.addChildren([talent, talentLead, freelanceDetail, newMatch, companies, companiesDetail, duplicatesRoute]),
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

export const CV_PERSON = {
  first_name: 'Grace',
  last_name: 'Hopper',
  email: 'grace.cv@example.org',
  phone: '+39 333 123 4567',
  city: 'Torino',
  linkedin_url: 'https://www.linkedin.com/in/grace-cv',
  other_links: ['https://github.com/grace-cv', 'https://github.com/ada'],
  companies: ['Acme Pagamenti', 'Bottega Digitale'],
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
export const VETTED_TALENT = { ...CARD_TALENT, vetted_at: '2026-09-25T09:00:00Z', has_anonymous_card: true }

/** A company request that came from the team builder's beta box (spec § 4.3). */
export const COMPANY_TEAM_BUILDER = { ...COMPANY_A, origin: 'team-builder', talent_cloud_grant: null }

/** The live grant `POST /api/hub/companies/c1/cloud` answers (REB-518). */
export const GRANT = {
  id: 'g1',
  user_id: 'u1',
  company_id: 'c1',
  company: 'Rossi Studio',
  contact_person: 'Mario Rossi',
  email: 'mario@rossi.it',
  granted_by: 'a1',
  granted_by_name: 'Ivan',
  granted_at: '2026-09-26T10:00:00Z',
  revoked_by: null,
  revoked_by_name: null,
  revoked_at: null,
}

/** Two live cards of one person (REB-800), as `GET /api/hub/talent/duplicati` reads them. */
export function person(id: string, firstName: string, last_name: string) {
  return {
    freelancer_id: id,
    user_id: `u-${id}`,
    first_name: firstName,
    last_name,
    email: `${firstName.toLowerCase()}@studio.it`,
    created_at: '2026-09-10T10:00:00Z',
    cv_uploaded_at: null,
    has_cv: false,
  }
}
export const LINKED_PAIR = {
  survivor: person('f2', 'Grace', 'Hopper'),
  absorbed: person('f1', 'Ada', 'Lovelace'),
  signals: ['linkedin'],
  mergeable: true,
}
export const NAME_PAIR = { ...LINKED_PAIR, survivor: person('f3', 'Alan', 'Turing'), signals: ['first_name'], mergeable: false }

// The stub goes first: it was installed over the test's own spy, and putting back the
// spy's value before the spy is restored would leave it in `fetch` for the next test.
export function resetMocks() {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
}
