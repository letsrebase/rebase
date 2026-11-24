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
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Area } from './Area'

vi.mock('@rebase/analytics/browser', () => ({
  capture: vi.fn(),
}))
import { capture } from '@rebase/analytics/browser'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const NO_CONTRACTS = { framework: null, previous_frameworks: [], letters: [] }
const NO_REFERRAL = { code: 'ABCDEFGH', rate_freelancer: '0.1000', rate_company: '0.3000', referred: [] }

/** `/me` answers `profile`; a card's page also reads its contracts (REB-392), and
 *  every page reads its own referral link (P-REB-44). A fresh Response per call,
 *  since a body can be read once. */
function meFetch(profile: unknown, contracts: unknown = NO_CONTRACTS, referral: unknown = NO_REFERRAL) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const path = String(input)
    if (path === '/api/hub/me/contracts') return answer(200, contracts)
    if (path === '/api/hub/me/referral') return answer(200, referral)
    return answer(200, profile)
  })
}

const PROFILE = {
  id: 'f1',
  first_name: 'Ada',
  last_name: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  phone: null,
  contact_points: [],
  role: 'member',
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
  has_card: true,
  cv_filename: 'Ada CV.pdf',
  cv_size: 2048,
  daily_rate: '450.00',
  job_title: 'Backend developer',
  remote: 'hybrid',
  links: ['https://github.com/ada'],
  complete: true,
  has_company: false,
  requests: [],
}

/** The card an admin wrote from Ada's signup (ORB-155): the person has yet to add the
 *  CV, the rate, the position and how she works. */
const INCOMPLETE = {
  ...PROFILE,
  cv_filename: null,
  cv_size: null,
  daily_rate: null,
  job_title: null,
  remote: null,
  complete: false,
}

/** An admin with no freelancer card at all (REB-279): `has_card` false, every card
 *  field blank, `complete` false -- the shape `MeRead` answers for a bare `users` row. */
const CARDLESS_ADMIN = {
  ...PROFILE,
  id: 'a1',
  first_name: 'Ivan',
  last_name: 'Fiore',
  email: 'ivan@rebase.it',
  linkedin_url: null,
  role: 'admin',
  has_card: false,
  cv_filename: null,
  cv_size: null,
  daily_rate: null,
  job_title: null,
  remote: null,
  links: [],
  complete: false,
}

/** A plain member with neither a freelancer card nor a company request (REB-385): the
 *  "Chi sei" fallback renders, and neither perk qualifies -- this is the case the
 *  freelancer-or-admin condition is meant to exclude, distinct from `CARDLESS_ADMIN`
 *  only in `role`. */
const NOBODY = {
  ...CARDLESS_ADMIN,
  id: 'n1',
  first_name: 'Nessuno',
  last_name: 'Speciale',
  email: 'nessuno@example.it',
  role: 'member',
}

/** The request a company filed first (REB-602), the older of two. */
const OLDER_REQUEST = {
  id: 'r1',
  requested_role: 'Backend developer',
  project: 'Serve un backend developer per tre mesi, da ottobre.',
  period_from: '2026-10-01',
  duration: '3 mesi',
  daily_budget: '500.00',
  remote: 'hybrid',
  onsite_days: 3,
  headcount: 2,
  created_at: '2026-09-20T10:00:00Z',
}

/** The one it filed afterwards, through «Richiedi una nuova figura». */
const NEWER_REQUEST = {
  id: 'r2',
  requested_role: 'Data engineer',
  project: 'Serve un data engineer per costruire la pipeline dei dati.',
  period_from: '2026-11-02',
  duration: '2 mesi',
  daily_budget: '550.00',
  remote: 'remote',
  onsite_days: null,
  headcount: 1,
  created_at: '2026-09-29T09:00:00Z',
}

/** A company contact with no freelancer card and one request (REB-314; REB-602 makes
 *  it a list): `has_card` false, `has_company` true, `requests` newest first. */
const COMPANY_ONLY = {
  ...PROFILE,
  id: 'c1',
  first_name: 'Wile',
  last_name: 'E.',
  email: 'wile@acme.it',
  linkedin_url: null,
  phone: '+39 345 1234567',
  has_card: false,
  cv_filename: null,
  cv_size: null,
  daily_rate: null,
  job_title: null,
  remote: null,
  links: [],
  complete: false,
  has_company: true,
  requests: [OLDER_REQUEST],
}

/** The same company after a second request: both are listed, the newest first. */
const COMPANY_TWO_REQUESTS = { ...COMPANY_ONLY, requests: [NEWER_REQUEST, OLDER_REQUEST] }

/** The request, on a person who also has a freelancer card (REB-314): both sections
 *  render together. */
const BOTH = { ...PROFILE, has_company: true, requests: [OLDER_REQUEST] }

function mount(path = '/me') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <Outlet /> })
  const index = createRoute({
    getParentRoute: () => me,
    path: '/',
    component: Area,
    validateSearch: (search: Record<string, unknown>): { denied?: true; github?: string } => ({
      denied: search.denied === true || search.denied === 'true' ? true : undefined,
      github: typeof search.github === 'string' ? search.github : undefined,
    }),
  })
  const edit = createRoute({ getParentRoute: () => me, path: '/edit', component: () => <h1>Modifica</h1> })
  const editLegalEntity = createRoute({
    getParentRoute: () => me,
    path: '/edit-company',
    component: () => <h1>Modifica azienda</h1>,
  })
  const editRequest = createRoute({
    getParentRoute: () => me,
    path: '/edit-company/$id',
    component: () => <h1>Modifica una richiesta</h1>,
  })
  const newRequestLegalEntity = createRoute({
    getParentRoute: () => me,
    path: '/new-company',
    component: () => <h1>Richiedi una nuova figura</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([
      me.addChildren([index, edit, editLegalEntity, editRequest, newRequestLegalEntity]),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return client
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

/** Opens one tab of the page by its name, the way a person clicks it. */
async function openTab(name: RegExp | string) {
  await userEvent.setup().click(await screen.findByRole('tab', { name }))
}

describe('/me, a card (REB-279: reads the merged `useMe`, gated on `ha_scheda`)', () => {
  it('shows the card’s answers in its tab, the facts in the header and the two perks in the rail', async () => {
    meFetch(PROFILE)
    mount()
    expect(await screen.findByRole('heading', { level: 1, name: 'Ada Lovelace' })).toBeInTheDocument()
    const header = screen.getByRole('banner')
    expect(within(header).getByText('Backend developer')).toBeInTheDocument()
    // The rate goes through the money format, never the raw decimal the API answers.
    expect(within(header).getByText(/^450,00\s€ al giorno$/)).toBeInTheDocument()
    expect(within(header).getByText('Ibrido')).toBeInTheDocument()
    expect(within(header).getByText('ada@studio.it')).toBeInTheDocument()
    expect(within(header).getByRole('link', { name: 'Modifica scheda' })).toHaveAttribute('href', '/me/edit')
    expect(within(header).getByRole('link', { name: /^CV · / })).toHaveAttribute('href', '/api/hub/me/cv')
    await openTab('La tua scheda')
    const card = screen.getByRole('region', { name: 'La tua scheda' })
    expect(within(card).getByText('Cosa fai')).toBeInTheDocument()
    expect(within(card).getByText(/^450,00\s€ al giorno$/)).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: /Ada CV\.pdf/ })).toHaveAttribute('href', '/api/hub/me/cv')
    const rail = screen.getByRole('complementary', { name: 'Segnalazioni e vantaggi' })
    expect(within(rail).getByRole('link', { name: /Apri PigroCRM/ })).toHaveAttribute(
      'href',
      'https://pigro.letsrebase.com/app/login',
    )
    expect(within(rail).getByRole('link', { name: /Scarica la guida/ })).toHaveAttribute('href', '/api/hub/me/guide')
    expect(within(rail).getByText('PDF, 6 pagine, 48 KB.')).toBeInTheDocument()
    // A complete card gets no reminder, and no access-rule banner either.
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByText('Nessun CV')).toBeNull()
  })

  it('keeps the referral link and the perks in the rail whichever tab is open', async () => {
    meFetch(PROFILE)
    mount()
    const rail = await screen.findByRole('complementary', { name: 'Segnalazioni e vantaggi' })
    for (const name of ['La tua scheda', 'Contratti', 'Recapiti']) {
      await openTab(name)
      expect(within(rail).getByRole('heading', { name: 'Il tuo link di segnalazione' })).toBeInTheDocument()
      expect(within(rail).getByRole('link', { name: /Apri PigroCRM/ })).toBeInTheDocument()
    }
  })

  it('asks the person to complete a card the admin wrote, and shows no CV link', async () => {
    meFetch(INCOMPLETE)
    mount()
    await screen.findByRole('tablist')
    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent('La tua scheda è incompleta.')
    expect(within(notice).getByRole('link', { name: 'Completa la scheda' })).toHaveAttribute('href', '/me/edit')
    expect(screen.getByText('Nessun CV')).toBeInTheDocument()
    expect(screen.getAllByRole('link').some((link) => link.getAttribute('href') === '/api/hub/me/cv')).toBe(false)
    // The unanswered questions read as dashes, not as a crash.
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3)
  })

  it('counts the guide on the click and leaves the download to the link', async () => {
    meFetch(PROFILE)
    mount()
    const link = await screen.findByRole('link', { name: /Scarica la guida/ })
    // jsdom cannot navigate; stopping the default here does not stop React's own handler.
    link.addEventListener('click', (event) => event.preventDefault())
    await userEvent.setup().click(link)
    expect(capture).toHaveBeenCalledWith('guide_downloaded')
    expect(link).toHaveAttribute('href', '/api/hub/me/guide')
  })
})

describe('/me, no card (REB-279: a card-less admin reads name, email and role, not null fields)', () => {
  it('shows no card, no «Modifica scheda» and no card tabs, and names the role in the header', async () => {
    meFetch(CARDLESS_ADMIN)
    mount()
    expect(await screen.findByRole('heading', { name: 'Ivan Fiore' })).toBeInTheDocument()
    expect(screen.getByText('ivan@rebase.it')).toBeInTheDocument()
    expect(screen.getByText('Amministratore')).toBeInTheDocument()
    expect(screen.queryByText('Cosa fai')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Modifica scheda' })).toBeNull()
    expect(screen.queryByText('Nessun CV')).toBeNull()
    // No company request either, and one tab only, so no tab bar.
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByRole('link', { name: /^Modifica la richiesta/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Richiedi una nuova figura/ })).toBeNull()
    // The perks stay visible with no card, because this fixture is an admin
    // (REB-385: freelancer-or-admin still gets both boxes).
    expect(screen.getByRole('link', { name: /Apri PigroCRM/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Scarica la guida/ })).toBeInTheDocument()
  })

  it('names a member with neither a card nor a request «Membro», with no perks (REB-385)', async () => {
    meFetch(NOBODY)
    mount()
    expect(await screen.findByRole('heading', { name: 'Nessuno Speciale' })).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Ruoli' })).getByText('Membro')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'I tuoi recapiti' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Apri PigroCRM/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Scarica la guida/ })).toBeNull()
  })
})

describe('/me, a company request (REB-314: reads `ha_azienda` independently of `ha_scheda`)', () => {
  it('shows the one request with its answers and its own edit link', async () => {
    meFetch(COMPANY_ONLY)
    mount()
    expect(await screen.findByRole('heading', { name: 'La tua richiesta' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Backend developer' })).toBeInTheDocument()
    expect(
      screen.getByText('Serve un backend developer per tre mesi, da ottobre.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/^1 ott 2026, 3 mesi$/)).toBeInTheDocument()
    expect(screen.getByText(/^500,00\s€ \/ giorno$/)).toBeInTheDocument()
    expect(screen.getByText('Ibrido · 3 giorni in sede')).toBeInTheDocument()
    expect(screen.getByText('2 persone')).toBeInTheDocument()
    expect(screen.getByText(/^Inviata il 20 set 2026$/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Modifica la richiesta 1 di 1: Backend developer' })).toHaveAttribute(
      'href',
      '/me/edit-company/r1',
    )
    expect(screen.getByRole('link', { name: /Richiedi una nuova figura/ })).toHaveAttribute(
      'href',
      '/me/new-company',
    )
    // No freelancer card: no wizard-shaped card section, and the "Chi sei" fallback
    // does not show either, since the company section already says who this is.
    expect(screen.queryByText('Come ti chiami?')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Modifica' })).toBeNull()
    expect(screen.queryByLabelText('Chi sei')).toBeNull()
    // A company-only referente qualifies as neither a freelancer nor an admin: no
    // PigroCRM or guide box (REB-385).
    expect(screen.queryByRole('link', { name: /Apri PigroCRM/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Scarica la guida/ })).toBeNull()
  })

  it('lists every request newest first, each opening its own edit page (REB-602)', async () => {
    meFetch(COMPANY_TWO_REQUESTS)
    mount()
    expect(await screen.findByRole('heading', { name: 'Le tue richieste' })).toBeInTheDocument()
    const [newer, older] = screen.getAllByRole('listitem') as [HTMLElement, HTMLElement]
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(within(newer).getByRole('heading', { name: 'Data engineer' })).toBeInTheDocument()
    expect(within(older).getByRole('heading', { name: 'Backend developer' })).toBeInTheDocument()
    expect(within(newer).getByRole('link', { name: /^Modifica/ })).toHaveAttribute(
      'href',
      '/me/edit-company/r2',
    )
    expect(within(older).getByRole('link', { name: /^Modifica/ })).toHaveAttribute(
      'href',
      '/me/edit-company/r1',
    )
    // Each card carries its own answers, so the older one is not the newer one's twin.
    expect(within(newer).getByText('Da remoto')).toBeInTheDocument()
    expect(within(newer).getByText('1 persona')).toBeInTheDocument()
    expect(within(older).getByText('Ibrido · 3 giorni in sede')).toBeInTheDocument()
    // One action for the section, not one per request.
    expect(screen.getAllByRole('link', { name: /Richiedi una nuova figura/ })).toHaveLength(1)
  })

  it('names the edit links apart when two requests are identical down to the minute they were filed', async () => {
    meFetch({ ...COMPANY_ONLY, requests: [{ ...OLDER_REQUEST, id: 'r3' }, OLDER_REQUEST] })
    mount()
    await screen.findByRole('heading', { name: 'Le tue richieste' })
    const names = screen
      .getAllByRole('link', { name: /^Modifica la richiesta/ })
      .map((link) => link.getAttribute('aria-label'))
    expect(names).toEqual([
      'Modifica la richiesta 1 di 2: Backend developer',
      'Modifica la richiesta 2 di 2: Backend developer',
    ])
  })

  it('gives a person with a card and a request both, each in its own tab (REB-314)', async () => {
    meFetch(BOTH)
    mount()
    await openTab('La tua scheda')
    expect(screen.getByText('Cosa fai')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Modifica scheda' })).toHaveAttribute('href', '/me/edit')
    await openTab(/^Richieste/)
    expect(screen.getByRole('heading', { name: 'La tua richiesta' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^Modifica la richiesta/ })).toHaveAttribute(
      'href',
      '/me/edit-company/r1',
    )
    expect(screen.getByRole('link', { name: /Richiedi una nuova figura/ })).toHaveAttribute(
      'href',
      '/me/new-company',
    )
    // The freelancer card alone is enough to qualify: both perks still show even
    // though this person is also a company referente (REB-385).
    expect(screen.getByRole('link', { name: /Apri PigroCRM/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Scarica la guida/ })).toBeInTheDocument()
  })
})

describe('/me, tabs for what the person has and the tab that waits for them (REB-1171)', () => {
  const APPROVED = {
    state: 'approved',
    submitted_at: '2026-11-12T10:00:00Z',
    expires_at: '2027-11-12T10:00:00Z',
    expired: false,
    decision_note: null,
    technologies: [],
    practices: [],
  }
  const cases: [string, unknown, string[] | null][] = [
    ['a card only', PROFILE, ['La tua scheda', 'Profilo di lavoro', 'Contratti', 'Recapiti']],
    ['a company only', COMPANY_ONLY, ['Richieste1', 'Recapiti']],
    ['a company with two requests', COMPANY_TWO_REQUESTS, ['Richieste2', 'Recapiti']],
    ['a card and a company', BOTH, ['La tua scheda', 'Profilo di lavoro', 'Richieste1', 'Contratti', 'Recapiti']],
    ['a card-less admin', CARDLESS_ADMIN, null],
    ['a plain member with nothing else', NOBODY, null],
  ]

  it.each(cases)('shows only the tabs that apply to %s', async (_name, profile, tabs) => {
    meFetch(profile)
    mount()
    await screen.findByRole('heading', { level: 1 })
    await screen.findByText('Nessuna segnalazione ancora.')
    if (tabs === null) {
      expect(screen.queryByRole('tablist')).toBeNull()
      expect(screen.getByRole('heading', { name: 'I tuoi recapiti' })).toBeInTheDocument()
    } else {
      expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(tabs)
    }
  })

  it('opens on the work profile while there is none to read', async () => {
    meFetch(PROFILE)
    mount()
    expect(await screen.findByRole('tab', { name: 'Profilo di lavoro' })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens on the card once the profile is approved, and says so in the header', async () => {
    meFetch({ ...PROFILE, work_profile: APPROVED })
    mount()
    expect(await screen.findByRole('tab', { name: 'La tua scheda' })).toHaveAttribute('aria-selected', 'true')
    expect(within(screen.getByRole('banner')).getByText('Profilo di lavoro approvato')).toBeInTheDocument()
  })

  it('opens on an incomplete card before the work profile', async () => {
    meFetch(INCOMPLETE)
    mount()
    expect(await screen.findByRole('tab', { name: 'La tua scheda' })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens on the requests for a company contact', async () => {
    meFetch(COMPANY_ONLY)
    mount()
    expect(await screen.findByRole('tab', { name: /^Richieste/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens on a rejected work profile, with the admin\'s note (REB-1127)', async () => {
    meFetch({
      ...PROFILE,
      work_profile: {
        state: 'rejected',
        submitted_at: '2026-11-12T10:00:00Z',
        expires_at: '2027-11-12T10:00:00Z',
        expired: false,
        decision_note: 'Manca un progetto con un link.',
        technologies: [],
        practices: [],
      },
    })
    mount()
    expect(await screen.findByRole('tab', { name: 'Profilo di lavoro' })).toHaveAttribute('aria-selected', 'true')
    const section = screen.getByRole('region', { name: 'Profilo di lavoro' })
    expect(within(section).getByText('Non approvato')).toBeInTheDocument()
    expect(within(section).getByText('Manca un progetto con un link.')).toBeInTheDocument()
  })

  it('keeps the tab a person chose over the one that waits for them', async () => {
    meFetch(PROFILE)
    mount()
    await openTab('Recapiti')
    expect(screen.getByRole('tab', { name: 'Recapiti' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'I tuoi recapiti' })).toBeInTheDocument()
  })

  it('waits for the contracts before choosing, and does not move when they change afterwards', async () => {
    const TO_SIGN = {
      ...NO_CONTRACTS,
      framework: { id: 'd1', kind: 'framework', label: 'Contratto quadro', number: null, status: 'sent', signed_at: null, signing_url: 'https://firma.letsrebase.com/sign/abc', has_pdf_signed: false, active: false },
    }
    let release: (value: Response) => void = () => {}
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input)
      if (path === '/api/hub/me/contracts') return new Promise<Response>((resolve) => { release = resolve })
      if (path === '/api/hub/me/referral') return answer(200, NO_REFERRAL)
      return answer(200, { ...PROFILE, work_profile: null })
    })
    const client = mount()
    await screen.findByRole('heading', { level: 1 })
    // Until the contracts answer, no tab is picked: the profile to send must not flash first.
    expect(screen.queryByRole('tablist')).toBeNull()
    release(answer(200, TO_SIGN))
    expect(await screen.findByRole('tab', { name: /^Contratti/ })).toHaveAttribute('aria-selected', 'true')
    // Signed in another tab: the data refreshes, the open tab stays.
    client.setQueryData(['me', 'contracts'], NO_CONTRACTS)
    expect(await screen.findByRole('tab', { name: 'Contratti' })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens on the card with GitHub\'s notice after a return from GitHub', async () => {
    meFetch({ ...PROFILE, github: { login: 'ada', scope: 'public', linked_at: '2026-11-24T10:00:00Z' } })
    mount('/me?github=linked')
    expect(await screen.findByRole('tab', { name: 'La tua scheda' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Account GitHub collegato.')).toBeInTheDocument()
  })

  it('keeps a contact being written when the person looks at another tab', async () => {
    meFetch(PROFILE)
    mount()
    await openTab('Recapiti')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Modifica recapiti' }))
    await user.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
    await user.type(screen.getByRole('textbox', { name: 'Valore del recapito 1' }), 'ada.casa@gmail.com')
    await openTab('La tua scheda')
    await openTab('Recapiti')
    expect(screen.getByRole('textbox', { name: 'Valore del recapito 1' })).toHaveValue('ada.casa@gmail.com')
  })
})

describe('/me, «Apri PigroCRM» (REB-602)', () => {
  it('opens PigroCRM in another tab and does not hand it the opener', async () => {
    meFetch(PROFILE)
    mount()
    const link = await screen.findByRole('link', { name: /Apri PigroCRM/ })
    expect(link).toHaveAttribute('target', '_blank')
    const rel = (link.getAttribute('rel') ?? '').split(/\s+/)
    expect(rel).toEqual(expect.arrayContaining(['noopener', 'noreferrer']))
    // The guide is a download, not a page: it stays in this tab.
    expect(screen.getByRole('link', { name: /Scarica la guida/ })).not.toHaveAttribute('target')
  })
})

describe('/me?denied=true (REB-279: AdminGuard bounces a signed-in non-admin here)', () => {
  it('shows a sentence instead of a blank screen or a raw refusal', async () => {
    meFetch(PROFILE)
    mount('/me?denied=true')
    await screen.findByRole('tablist')
    expect(screen.getByRole('status')).toHaveTextContent('riservata a chi amministra')
  })

  it('says nothing extra without the flag', async () => {
    meFetch(PROFILE)
    mount('/me')
    await screen.findByRole('tablist')
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('/me, «Contratti» (REB-392: on a card, never on a company-only profile)', () => {
  it('shows the section on a card, with what there is to sign', async () => {
    meFetch(PROFILE, {
      framework: {
        id: 'd1',
        kind: 'framework',
        number: null,
        status: 'sent',
        customer: null,
        start: null,
        fine: null,
        sent_at: '2026-09-23T10:00:00Z',
        signed_at: null,
        signing_url: 'https://firma.letsrebase.com/sign/abc',
        has_pdf_signed: false,
        active: false,
        renewal: null,
        last_notice_day: null,
      },
      previous_frameworks: [],
      letters: [],
    })
    mount()
    expect(await screen.findByRole('heading', { name: 'Contratti' })).toBeInTheDocument()
    // Something to sign opens «Contratti» and marks its tab.
    expect(await screen.findByRole('tab', { name: /^Contratti\s*Da firmare$/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(await screen.findByRole('link', { name: 'Firma il contratto quadro' })).toHaveAttribute(
      'href',
      'https://firma.letsrebase.com/sign/abc',
    )
  })

  it('has no «Contratti» for a company-only profile', async () => {
    meFetch(COMPANY_ONLY)
    mount()
    await screen.findByRole('heading', { name: 'La tua richiesta' })
    expect(screen.queryByRole('heading', { name: 'Contratti' })).toBeNull()
  })
})

/** The header line that names what this person is (REB-601): one badge per role held. */
describe('/me, the roles in the header (REB-601)', () => {
  async function roles(profile: object): Promise<string[]> {
    meFetch(profile)
    mount()
    await screen.findByRole('heading', { level: 1 })
    const group = screen.queryByRole('group', { name: 'Ruoli' })
    return group ? Array.from(group.children).map((badge) => badge.textContent ?? '') : []
  }

  it('names all three for an admin with a card and a company request', async () => {
    expect(await roles({ ...BOTH, role: 'admin' })).toEqual(['Freelance', 'Azienda', 'Amministratore'])
  })

  it('names an admin who also has a card, with no company request', async () => {
    expect(await roles({ ...PROFILE, role: 'admin' })).toEqual(['Freelance', 'Amministratore'])
  })

  it('names only the company for a company-only member', async () => {
    expect(await roles(COMPANY_ONLY)).toEqual(['Azienda'])
  })

  it('names only the freelancer for a member with just a card', async () => {
    expect(await roles(PROFILE)).toEqual(['Freelance'])
  })

  it('names a member with neither «Membro»', async () => {
    expect(await roles(NOBODY)).toEqual(['Membro'])
  })

  it('names a card-less admin once', async () => {
    expect(await roles(CARDLESS_ADMIN)).toEqual(['Amministratore'])
    expect(screen.queryByText('Ruolo')).toBeNull()
  })
})
