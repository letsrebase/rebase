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
  progetto: null,
  periodo_da: null,
  durata: null,
  budget_giornaliero: null,
  azienda_remoto: null,
  azienda_giorni_presenza: null,
  azienda_numero_risorse: null,
  azienda_figura_richiesta: null,
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

/** A company contact with no freelancer card, and the referente's most recent
 *  request (REB-314; REB-380 adds the last four fields): `ha_scheda` false,
 *  `ha_azienda` true, the project's own seven answers filled in. */
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
  progetto: 'Serve un backend developer per tre mesi, da ottobre.',
  periodo_da: '2026-10-01',
  durata: '3 mesi',
  budget_giornaliero: '500.00',
  azienda_remoto: 'ibrido',
  azienda_giorni_presenza: 3,
  azienda_numero_risorse: 2,
  azienda_figura_richiesta: 'Backend developer',
}

/** The same request, on a person who also has a freelancer card (REB-314): both
 *  sections render together. */
const BOTH = {
  ...PROFILE,
  ha_azienda: true,
  progetto: COMPANY_ONLY.progetto,
  periodo_da: COMPANY_ONLY.periodo_da,
  durata: COMPANY_ONLY.durata,
  budget_giornaliero: COMPANY_ONLY.budget_giornaliero,
  azienda_remoto: COMPANY_ONLY.azienda_remoto,
  azienda_giorni_presenza: COMPANY_ONLY.azienda_giorni_presenza,
  azienda_numero_risorse: COMPANY_ONLY.azienda_numero_risorse,
  azienda_figura_richiesta: COMPANY_ONLY.azienda_figura_richiesta,
}

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
  const nuovaRichiestaAzienda = createRoute({
    getParentRoute: () => me,
    path: '/new-company',
    component: () => <h1>Richiedi una nuova figura</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([me.addChildren([index, edit, modificaAzienda, nuovaRichiestaAzienda])]),
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
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
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
      'https://pigro.letsrebase.com/app/register',
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

  it('gives both perk cards a growing description block, so a footer note cannot shift the button (REB-312)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
    mount()
    const pigrocrmButton = await screen.findByRole('link', { name: /Apri PigroCRM/ })
    const guideButton = screen.getByRole('link', { name: /Scarica la guida/ })
    const pigrocrmCard = pigrocrmButton.parentElement
    const guideCard = guideButton.parentElement
    // jsdom does not lay out flexbox, so the real proof that the two buttons land at
    // the same height is the live browser screenshot; here we assert the structural
    // fix instead: both cards wrap their eyebrow/title/description in a `flex-1`
    // block that absorbs whatever space is left above the button (rather than the
    // button itself carrying `mt-auto`), and both cards reserve the same footer slot
    // below the button -- visible with its text on the guide card, present but
    // `invisible` on the card with no footer note -- so a trailing note cannot push
    // one button higher than the other.
    expect(pigrocrmCard?.querySelector(':scope > .flex-1')).not.toBeNull()
    expect(guideCard?.querySelector(':scope > .flex-1')).not.toBeNull()
    expect(pigrocrmButton.className).not.toMatch(/\bmt-auto\b/)
    expect(guideButton.className).not.toMatch(/\bmt-auto\b/)
    const pigrocrmFooter = pigrocrmCard?.querySelector(':scope > p:last-child')
    const guideFooter = guideCard?.querySelector(':scope > p:last-child')
    expect(pigrocrmFooter).toHaveClass('invisible')
    expect(guideFooter).not.toHaveClass('invisible')
    expect(guideFooter).toHaveTextContent('PDF, 6 pagine, 48 KB.')
  })

  it('asks the person to complete a card the admin wrote, and shows no CV link', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, INCOMPLETE))
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
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
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
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, CARDLESS_ADMIN))
    mount()
    expect(await screen.findByRole('heading', { name: 'Ivan Fiore' })).toBeInTheDocument()
    expect(screen.getByText('ivan@rebase.it')).toBeInTheDocument()
    expect(screen.getByText('Amministratore')).toBeInTheDocument()
    expect(screen.queryByText('Come ti chiami?')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Modifica' })).toBeNull()
    expect(screen.queryByText('Nessun CV')).toBeNull()
    // No company request either: neither section replaces the bare identity one.
    expect(screen.queryByText('La tua richiesta più recente')).toBeNull()
    expect(screen.queryByRole('link', { name: /Modifica richiesta/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Richiedi una nuova figura/ })).toBeNull()
    // The perks stay unconditional even with no card.
    expect(screen.getByRole('link', { name: /Apri PigroCRM/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Scarica la guida/ })).toBeInTheDocument()
  })
})

describe('/me, a company request (REB-314: reads `ha_azienda` independently of `ha_scheda`)', () => {
  it('shows the project under the wizard’s own questions, with its own edit link', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, COMPANY_ONLY))
    mount()
    expect(await screen.findByText('La tua richiesta più recente')).toBeInTheDocument()
    expect(
      screen.getByText('Serve un backend developer per tre mesi, da ottobre.'),
    ).toBeInTheDocument()
    expect(screen.getByText('dal 2026-10-01, 3 mesi')).toBeInTheDocument()
    expect(screen.getByText('500.00 € / giorno')).toBeInTheDocument()
    expect(screen.getByText('Backend developer')).toBeInTheDocument()
    expect(screen.getByText('Ibrido · 3 giorni in sede')).toBeInTheDocument()
    expect(screen.getByText('2 persone')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Modifica richiesta/ })).toHaveAttribute(
      'href',
      '/me/edit-company',
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
  })

  it('renders alongside the freelancer card when a person has both (REB-314)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, BOTH))
    mount()
    expect(await screen.findByText('Come ti chiami?')).toBeInTheDocument()
    expect(screen.getByText('La tua richiesta più recente')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Modifica' })).toHaveAttribute('href', '/me/edit')
    expect(screen.getByRole('link', { name: /Modifica richiesta/ })).toHaveAttribute(
      'href',
      '/me/edit-company',
    )
    expect(screen.getByRole('link', { name: /Richiedi una nuova figura/ })).toHaveAttribute(
      'href',
      '/me/new-company',
    )
  })
})

describe('/me?negato=true (REB-279: AdminGuard bounces a signed-in non-admin here)', () => {
  it('shows a sentence instead of a blank screen or a raw refusal', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
    mount('/me?negato=true')
    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('riservata a chi amministra')
  })

  it('says nothing extra without the flag', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
    mount('/me')
    await screen.findAllByText('Ada Lovelace')
    expect(screen.queryByRole('status')).toBeNull()
  })
})
