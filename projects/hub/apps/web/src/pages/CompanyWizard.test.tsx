import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadDraft } from '@/wizard/draft'
import { COMPANY_DRAFT_KEY, CompanyWizard } from './CompanyWizard'

vi.mock('@rebase/analytics/browser', () => ({
  capture: vi.fn(),
  distinctId: vi.fn(() => 'anon-1'),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}))
import { capture } from '@rebase/analytics/browser'

/** The wizard mounted on its own little router, so `navigate` has somewhere to go. */
function mount(path = '/companies') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const companies = createRoute({ getParentRoute: () => root, path: '/companies', component: CompanyWizard })
  const thanks = createRoute({
    getParentRoute: () => root,
    path: '/thanks',
    validateSearch: (s: Record<string, unknown>) => ({ who: String(s.who ?? '') }),
    component: () => <h1>Grazie</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([companies, thanks]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(<RouterProvider router={router} />)
  return router
}

function captured(event: string) {
  return vi
    .mocked(capture)
    .mock.calls.filter(([name]) => name === event)
    .map(([, properties]) => properties)
}

// The draft lives in `localStorage`, which jsdom keeps across the tests of one file;
// the origin the page read lives in `sessionStorage` (`resolveOrigin`), the same way.
beforeEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  window.localStorage.clear()
  window.sessionStorage.clear()
})

describe('CompanyWizard', () => {
  it('walks the eight questions, posts the request and reports the funnel as azienda (ORB-185)', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()

    await user.type(await screen.findByLabelText('Azienda'), 'ACME Srl{Enter}')
    expect(captured('wizard_started')).toEqual([{ kind: 'company' }])
    await user.type(screen.getByLabelText('Figura richiesta'), 'Backend developer{Enter}')
    await user.type(screen.getByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it')
    await user.type(screen.getByLabelText('Telefono'), '+39 345 1234567{Enter}')
    await user.type(screen.getByLabelText('Progetto'), 'Dobbiamo rifare il backend del portale clienti.')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Da quando'), '2026-10-01')
    await user.type(screen.getByLabelText('Per quanto'), '3 mesi{Enter}')
    await user.type(screen.getByLabelText('Budget a giornata'), '500{Enter}')
    await user.click(screen.getByRole('radio', { name: /Ibrido/ }))
    await user.type(screen.getByLabelText('Giorni in sede a settimana'), '3')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Numero di persone'), '2{Enter}')
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
    expect(captured('wizard_step').map((p) => p?.step)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8])
    expect(captured('wizard_step').map((p) => p?.screen)).toEqual([
      'company_name',
      'requested_role',
      'contact_person',
      'project',
      'period_from',
      'daily_budget',
      'remote',
      'headcount',
      'recap',
    ])
    expect(captured('wizard_step')[0]).toEqual({
      kind: 'company',
      step: 0,
      steps: 8,
      screen: 'company_name',
    })
    expect(captured('wizard_completed')).toEqual([])

    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    expect(router.state.location.search).toEqual({ who: 'company' })
    expect(captured('wizard_completed')).toEqual([{ kind: 'company' }])

    const [url, init] = fetchSpy.mock.calls[0]!
    expect(url).toBe('/api/hub/companies')
    expect(JSON.parse(init?.body as string)).toMatchObject({
      company_name: 'ACME Srl',
      requested_role: 'Backend developer',
      phone: '+39 345 1234567',
      daily_budget: '500.00',
      remote: 'hybrid',
      onsite_days: 3,
      headcount: '2',
    })
  })
})

describe('CompanyWizard, the budget typed the Italian way (REB-485)', () => {
  it('refuses a budget whose thousands put it over the ceiling, and posts «1.500» as 1500', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()

    await user.type(await screen.findByLabelText('Azienda'), 'ACME Srl{Enter}')
    await user.type(screen.getByLabelText('Figura richiesta'), 'Backend developer{Enter}')
    await user.type(screen.getByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it')
    await user.type(screen.getByLabelText('Telefono'), '+39 345 1234567{Enter}')
    await user.type(screen.getByLabelText('Progetto'), 'Dobbiamo rifare il backend del portale clienti.')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Da quando'), '2026-10-01')
    await user.type(screen.getByLabelText('Per quanto'), '3 mesi{Enter}')
    // 150000, over the ceiling: read as 150 it would have gone through.
    await user.type(screen.getByLabelText('Budget a giornata'), '150.000{Enter}')
    expect(screen.getByText('Serve una cifra, in euro.')).toBeInTheDocument()
    // The field is mounted again once it turns invalid, so it is looked up again.
    await user.clear(screen.getByLabelText('Budget a giornata'))
    await user.type(screen.getByLabelText('Budget a giornata'), '1.500{Enter}')
    await user.click(screen.getByRole('radio', { name: /Da remoto/ }))
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Numero di persone'), '2{Enter}')
    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))

    expect(JSON.parse(fetchSpy.mock.calls[0]![1]?.body as string).daily_budget).toBe('1500.00')
  })
})

describe('CompanyWizard, the draft and the intro (REB-215)', () => {
  it('introduces rebase above the first question', async () => {
    mount()
    expect(await screen.findByRole('complementary', { name: 'Cos’è rebase' })).toHaveTextContent(
      '8 domande',
    )
  })

  it('picks the answers up on the next visit and forgets them once sent', async () => {
    const user = userEvent.setup()
    mount()
    await user.type(await screen.findByLabelText('Azienda'), 'ACME Srl{Enter}')
    expect(loadDraft(COMPANY_DRAFT_KEY)).toMatchObject({ index: 1, value: { company_name: 'ACME Srl' } })

    cleanup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()
    expect(await screen.findByRole('note', { name: 'Risposte ritrovate' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Figura richiesta'), 'Backend developer{Enter}')
    await user.type(screen.getByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it')
    await user.type(screen.getByLabelText('Telefono'), '+39 345 1234567{Enter}')
    await user.type(screen.getByLabelText('Progetto'), 'Dobbiamo rifare il backend del portale clienti.')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Da quando'), '2026-10-01')
    await user.type(screen.getByLabelText('Per quanto'), '3 mesi{Enter}')
    await user.type(screen.getByLabelText('Budget a giornata'), '500{Enter}')
    await user.click(screen.getByRole('radio', { name: /Da remoto/ }))
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Numero di persone'), '2{Enter}')
    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    expect(loadDraft(COMPANY_DRAFT_KEY)).toBeNull()
    const body = JSON.parse(fetchSpy.mock.calls[0]![1]?.body as string)
    expect(body.company_name).toBe('ACME Srl')
    expect(body.distinct_id).toBe('anon-1')
  })
})

describe('CompanyWizard, opened from the team builder (REB-518)', () => {
  it('says above the form that the request is for the talent cloud', async () => {
    mount('/companies?from=team-builder')
    const note = await screen.findByRole('note', { name: 'Talent cloud' })
    expect(note).toHaveTextContent('Stai chiedendo l’accesso al talent cloud.')
    expect(note).toHaveTextContent('Compila la richiesta e ti ricontattiamo noi per aprirtelo.')
  })

  it('says nothing of the kind from any other page', async () => {
    mount('/companies?from=home')
    await screen.findByRole('complementary', { name: 'Cos’è rebase' })
    expect(screen.queryByRole('note', { name: 'Talent cloud' })).toBeNull()
  })
})

describe('CompanyWizard, the draft a browser kept before the 2026-11 rename', () => {
  it('picks up a draft left under `rebase.wizard.azienda`, with its Italian answers, and moves it', async () => {
    window.localStorage.setItem(
      'rebase.wizard.azienda',
      JSON.stringify({ v: 1, savedAt: Date.now(), index: 1, value: { 'nome_azienda': 'ACME Srl', 'remoto': 'ibrido' } }),
    )
    mount()
    expect(await screen.findByRole('note', { name: 'Risposte ritrovate' })).toBeInTheDocument()
    expect(window.localStorage.getItem('rebase.wizard.azienda')).toBeNull()
    expect(loadDraft(COMPANY_DRAFT_KEY)).toMatchObject({ index: 1, value: { company_name: 'ACME Srl', remote: 'hybrid' } })
  })
})

