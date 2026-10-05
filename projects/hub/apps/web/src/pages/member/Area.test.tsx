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

const NO_CONTRACTS = { quadro: null, quadri_precedenti: [], lettere: [] }
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
  nome: 'Ada',
  cognome: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  telefono: null,
  role: 'member',
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
  ha_scheda: true,
  cv_filename: 'Ada CV.pdf',
  cv_size: 2048,
  tariffa_giornaliera: '450.00',
  posizione: 'Backend developer',
  remoto: 'ibrido',
  links: ['https://github.com/ada'],
  completa: true,
  ha_azienda: false,
  richieste: [],
}

/** The card an admin wrote from Ada's signup (ORB-155): the person has yet to add the
 *  CV, the rate, the position and how she works. */
const INCOMPLETE = {
  ...PROFILE,
  cv_filename: null,
  cv_size: null,
  tariffa_giornaliera: null,
  posizione: null,
  remoto: null,
  completa: false,
}

/** An admin with no freelancer card at all (REB-279): `ha_scheda` false, every card
 *  field blank, `completa` false -- the shape `MeRead` answers for a bare `users` row. */
const CARDLESS_ADMIN = {
  ...PROFILE,
  id: 'a1',
  nome: 'Ivan',
  cognome: 'Fiore',
  email: 'ivan@rebase.it',
  linkedin_url: null,
  role: 'admin',
  ha_scheda: false,
  cv_filename: null,
  cv_size: null,
  tariffa_giornaliera: null,
  posizione: null,
  remoto: null,
  links: [],
  completa: false,
}

/** A plain member with neither a freelancer card nor a company request (REB-385): the
 *  "Chi sei" fallback renders, and neither perk qualifies -- this is the case the
 *  freelancer-or-admin condition is meant to exclude, distinct from `CARDLESS_ADMIN`
 *  only in `role`. */
const NOBODY = {
  ...CARDLESS_ADMIN,
  id: 'n1',
  nome: 'Nessuno',
  cognome: 'Speciale',
  email: 'nessuno@example.it',
  role: 'member',
}

/** The request a company filed first (REB-602), the older of two. */
const OLDER_REQUEST = {
  id: 'r1',
  figura_richiesta: 'Backend developer',
  progetto: 'Serve un backend developer per tre mesi, da ottobre.',
  periodo_da: '2026-10-01',
  durata: '3 mesi',
  budget_giornaliero: '500.00',
  remoto: 'ibrido',
  giorni_presenza: 3,
  numero_risorse: 2,
  created_at: '2026-09-20T10:00:00Z',
}

/** The one it filed afterwards, through «Richiedi una nuova figura». */
const NEWER_REQUEST = {
  id: 'r2',
  figura_richiesta: 'Data engineer',
  progetto: 'Serve un data engineer per costruire la pipeline dei dati.',
  periodo_da: '2026-11-02',
  durata: '2 mesi',
  budget_giornaliero: '550.00',
  remoto: 'remoto',
  giorni_presenza: null,
  numero_risorse: 1,
  created_at: '2026-09-29T09:00:00Z',
}

/** A company contact with no freelancer card and one request (REB-314; REB-602 makes
 *  it a list): `ha_scheda` false, `ha_azienda` true, `richieste` newest first. */
const COMPANY_ONLY = {
  ...PROFILE,
  id: 'c1',
  nome: 'Wile',
  cognome: 'E.',
  email: 'wile@acme.it',
  linkedin_url: null,
  telefono: '+39 345 1234567',
  ha_scheda: false,
  cv_filename: null,
  cv_size: null,
  tariffa_giornaliera: null,
  posizione: null,
  remoto: null,
  links: [],
  completa: false,
  ha_azienda: true,
  richieste: [OLDER_REQUEST],
}

/** The same company after a second request: both are listed, the newest first. */
const COMPANY_TWO_REQUESTS = { ...COMPANY_ONLY, richieste: [NEWER_REQUEST, OLDER_REQUEST] }

/** The request, on a person who also has a freelancer card (REB-314): both sections
 *  render together. */
const BOTH = { ...PROFILE, ha_azienda: true, richieste: [OLDER_REQUEST] }

function mount(path = '/me') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <Outlet /> })
  const index = createRoute({
    getParentRoute: () => me,
    path: '/',
    component: Area,
    validateSearch: (search: Record<string, unknown>): { negato?: true } => ({
      negato: search.negato === true || search.negato === 'true' ? true : undefined,
    }),
  })
  const edit = createRoute({ getParentRoute: () => me, path: '/edit', component: () => <h1>Modifica</h1> })
  const modificaAzienda = createRoute({
    getParentRoute: () => me,
    path: '/edit-company',
    component: () => <h1>Modifica azienda</h1>,
  })
  const modificaRichiesta = createRoute({
    getParentRoute: () => me,
    path: '/edit-company/$id',
    component: () => <h1>Modifica una richiesta</h1>,
  })
  const nuovaRichiestaAzienda = createRoute({
    getParentRoute: () => me,
    path: '/new-company',
    component: () => <h1>Richiedi una nuova figura</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([
      me.addChildren([index, edit, modificaAzienda, modificaRichiesta, nuovaRichiestaAzienda]),
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

describe('/me, a card (REB-279: reads the merged `useMe`, gated on `ha_scheda`)', () => {
  it('shows the answers under the wizard’s questions, the CV and the two perks', async () => {
    meFetch(PROFILE)
    mount()
    // The name is both the heading and the answer to the first question.
    expect(await screen.findAllByText('Ada Lovelace')).not.toHaveLength(0)
    expect(screen.getByText('Come ti chiami?')).toBeInTheDocument()
    expect(screen.getByText('450.00 € / giorno')).toBeInTheDocument()
    expect(screen.getByText('Ibrido')).toBeInTheDocument()
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Ada CV\.pdf/ })).toHaveAttribute('href', '/api/hub/me/cv')
    expect(screen.getByRole('link', { name: /Apri PigroCRM/ })).toHaveAttribute(
      'href',
      'https://pigro.letsrebase.com/app/login',
    )
    expect(screen.getByRole('link', { name: /Scarica la guida/ })).toHaveAttribute(
      'href',
      '/api/hub/me/guide',
    )
    expect(screen.getByText('PDF, 6 pagine, 48 KB.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Modifica' })).toHaveAttribute('href', '/me/edit')
    // A complete card gets no reminder, and no access-rule banner either.
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByText('Nessun CV')).toBeNull()
  })

  it('says the guide\'s size under its button, and gives the PigroCRM card no filler footer', async () => {
    meFetch(PROFILE)
    mount()
    const pigrocrmButton = await screen.findByRole('link', { name: /Apri PigroCRM/ })
    const guideButton = screen.getByRole('link', { name: /Scarica la guida/ })
    const pigrocrmCard = pigrocrmButton.parentElement
    const guideCard = guideButton.parentElement
    // The two boxes stack one per row (REB-641), so the invisible footer REB-312 kept on
    // the PigroCRM card to level its button with the guide's is gone: a filler line
    // under a button that has nothing beside it is only an empty row.
    expect(pigrocrmCard?.lastElementChild).toBe(pigrocrmButton)
    const guideFooter = guideCard?.querySelector(':scope > p:last-child')
    expect(guideFooter).toHaveTextContent('PDF, 6 pagine, 48 KB.')
  })

  it('asks the person to complete a card the admin wrote, and shows no CV link', async () => {
    meFetch(INCOMPLETE)
    mount()
    const notice = await screen.findByRole('status')
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
    expect(capture).toHaveBeenCalledWith('guida_scaricata')
    expect(link).toHaveAttribute('href', '/api/hub/me/guide')
  })
})

describe('/me, no card (REB-279: a card-less admin reads name, email and role, not null fields)', () => {
  it('shows no wizard-shaped section, no Modifica link, and the role instead', async () => {
    meFetch(CARDLESS_ADMIN)
    mount()
    expect(await screen.findByRole('heading', { name: 'Ivan Fiore' })).toBeInTheDocument()
    expect(screen.getByText('ivan@rebase.it')).toBeInTheDocument()
    expect(screen.getByText('Amministratore')).toBeInTheDocument()
    expect(screen.queryByText('Come ti chiami?')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Modifica' })).toBeNull()
    expect(screen.queryByText('Nessun CV')).toBeNull()
    // No company request either: neither section replaces the bare identity one.
    expect(screen.queryByRole('heading', { name: /^(La tua richiesta|Le tue richieste)$/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /^Modifica la richiesta/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Richiedi una nuova figura/ })).toBeNull()
    // The perks stay visible with no card, because this fixture is an admin
    // (REB-385: freelancer-or-admin still gets both boxes).
    expect(screen.getByRole('link', { name: /Apri PigroCRM/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Scarica la guida/ })).toBeInTheDocument()
  })

  it('shows the "Chi sei" fallback and no perks for a member with neither a card nor a request (REB-385)', async () => {
    meFetch(NOBODY)
    mount()
    expect(await screen.findByLabelText('Chi sei')).toBeInTheDocument()
    expect(screen.getByText('Membro')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Apri PigroCRM/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Scarica la guida/ })).toBeNull()
  })
})

/** The stack below the header (REB-641): one column, and each of its children is one
 *  section, so no two share a row. */
function stack(): HTMLElement {
  const header = screen.getByRole('heading', { level: 1 }).closest('header')
  return header?.nextElementSibling as HTMLElement
}

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
    meFetch({ ...COMPANY_ONLY, richieste: [{ ...OLDER_REQUEST, id: 'r3' }, OLDER_REQUEST] })
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

  it('renders alongside the freelancer card when a person has both (REB-314)', async () => {
    meFetch(BOTH)
    mount()
    expect(await screen.findByText('Come ti chiami?')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'La tua richiesta' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Modifica' })).toHaveAttribute('href', '/me/edit')
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

describe('/me, one column in a fixed order (REB-641), the referral panel first (REB-610)', () => {
  const PERKS = ['PigroCRM è tuo, gratis', 'I primi passi da freelance']
  const cases: [string, unknown, string[]][] = [
    ['a card only', PROFILE, ['La tua scheda', ...PERKS, 'Contratti']],
    ['a company only', COMPANY_ONLY, ['La tua richiesta']],
    ['a company with two requests', COMPANY_TWO_REQUESTS, ['Le tue richieste']],
    ['a card and a company', BOTH, ['La tua scheda', 'La tua richiesta', ...PERKS, 'Contratti']],
    ['a card-less admin', CARDLESS_ADMIN, PERKS],
    ['a plain member with nothing else', NOBODY, []],
  ]

  it.each(cases)('reads referral, card, requests, perks, contracts for %s', async (_name, profile, after) => {
    meFetch(profile)
    mount()
    await screen.findByRole('heading', { level: 1 })
    // The referral panel reads its own route: wait for it, so the page is settled.
    await screen.findByText('Nessuna segnalazione ancora.')
    const headings = screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)
    expect(headings).toEqual(['Il tuo link di segnalazione', ...after])
  })

  it('puts every section in a row of its own, the two perks included', async () => {
    meFetch(BOTH)
    mount()
    await screen.findByText('Nessuna segnalazione ancora.')
    const rows = Array.from(stack().children) as HTMLElement[]
    // Five sections, one per row: referral, card, request, perks, contracts.
    expect(rows.map((row) => row.tagName)).toEqual(['SECTION', 'SECTION', 'SECTION', 'SECTION', 'SECTION'])
    expect(rows[0]).toContainElement(screen.getByRole('heading', { name: 'Il tuo link di segnalazione' }))
    expect(rows[4]).toContainElement(screen.getByRole('heading', { name: 'Contratti' }))
    // The perks are two boxes in one section, stacked at every viewport: no column
    // class and no responsive variant on the section or on the stack.
    const perks = screen.getByLabelText('I tuoi vantaggi')
    expect(perks).toBe(rows[3])
    expect(perks.className).not.toMatch(/grid-cols-|\b(sm|md|lg|xl|2xl):/)
    expect(stack().className).not.toMatch(/grid-cols-|\b(sm|md|lg|xl|2xl):/)
  })

  it('shows the «Chi sei» fallback in the requests\' place, and nothing after it', async () => {
    meFetch(NOBODY)
    mount()
    await screen.findByText('Nessuna segnalazione ancora.')
    const rows = Array.from(stack().children) as HTMLElement[]
    expect(rows).toHaveLength(2)
    expect(rows[1]).toBe(screen.getByLabelText('Chi sei'))
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

describe('/me?negato=true (REB-279: AdminGuard bounces a signed-in non-admin here)', () => {
  it('shows a sentence instead of a blank screen or a raw refusal', async () => {
    meFetch(PROFILE)
    mount('/me?negato=true')
    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('riservata a chi amministra')
  })

  it('says nothing extra without the flag', async () => {
    meFetch(PROFILE)
    mount('/me')
    await screen.findAllByText('Ada Lovelace')
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('/me, «Contratti» (REB-392: on a card, never on a company-only profile)', () => {
  it('shows the section on a card, with what there is to sign', async () => {
    meFetch(PROFILE, {
      quadro: {
        id: 'd1',
        kind: 'quadro',
        numero: null,
        stato: 'inviato',
        cliente: null,
        inizio: null,
        fine: null,
        sent_at: '2026-09-23T10:00:00Z',
        signed_at: null,
        signing_url: 'https://firma.letsrebase.com/sign/abc',
        ha_pdf_firmato: false,
        attivo: false,
        rinnovo: null,
        ultimo_giorno_disdetta: null,
      },
      quadri_precedenti: [],
      lettere: [],
    })
    mount()
    expect(await screen.findByRole('heading', { name: 'Contratti' })).toBeInTheDocument()
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
