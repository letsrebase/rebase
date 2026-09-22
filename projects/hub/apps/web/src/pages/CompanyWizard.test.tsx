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
    validateSearch: (s: Record<string, unknown>) => ({ chi: String(s.chi ?? '') }),
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

// The draft lives in `localStorage`, which jsdom keeps across the tests of one file.
beforeEach(() => window.localStorage.clear())

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  window.localStorage.clear()
})

describe('CompanyWizard', () => {
  it('walks the five questions, posts the request and reports the funnel as azienda (ORB-185)', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()

    await user.type(await screen.findByLabelText('Azienda'), 'ACME Srl{Enter}')
    expect(captured('wizard_iniziato')).toEqual([{ tipo: 'azienda' }])
    await user.type(screen.getByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it{Enter}')
    await user.type(screen.getByLabelText('Progetto'), 'Dobbiamo rifare il backend del portale clienti.')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Da quando'), '2026-10-01')
    await user.type(screen.getByLabelText('Per quanto'), '3 mesi{Enter}')
    await user.type(screen.getByLabelText('Budget a giornata'), '500{Enter}')
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
    expect(captured('wizard_passo').map((p) => p?.passo)).toEqual([0, 1, 2, 3, 4, 5])
    expect(captured('wizard_passo').map((p) => p?.schermata)).toEqual([
      'nome_azienda',
      'referente',
      'progetto',
      'periodo_da',
      'budget_giornaliero',
      'riepilogo',
    ])
    expect(captured('wizard_passo')[0]).toEqual({
      tipo: 'azienda',
      passo: 0,
      passi: 5,
      schermata: 'nome_azienda',
    })
    expect(captured('wizard_completato')).toEqual([])

    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    expect(router.state.location.search).toEqual({ chi: 'azienda' })
    expect(captured('wizard_completato')).toEqual([{ tipo: 'azienda' }])

    const [url, init] = fetchSpy.mock.calls[0]!
    expect(url).toBe('/api/hub/companies')
    expect(JSON.parse(init?.body as string)).toMatchObject({ nome_azienda: 'ACME Srl', budget_giornaliero: '500' })
  })
})

describe('CompanyWizard, the draft and the intro (REB-215)', () => {
  it('introduces rebase above the first question', async () => {
    mount()
    expect(await screen.findByRole('complementary', { name: 'Cos’è rebase' })).toHaveTextContent('5 domande')
  })

  it('picks the answers up on the next visit and forgets them once sent', async () => {
    const user = userEvent.setup()
    mount()
    await user.type(await screen.findByLabelText('Azienda'), 'ACME Srl{Enter}')
    expect(loadDraft(COMPANY_DRAFT_KEY)).toMatchObject({ index: 1, value: { nome_azienda: 'ACME Srl' } })

    cleanup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()
    expect(await screen.findByRole('note', { name: 'Risposte ritrovate' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it{Enter}')
    await user.type(screen.getByLabelText('Progetto'), 'Dobbiamo rifare il backend del portale clienti.')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.type(screen.getByLabelText('Da quando'), '2026-10-01')
    await user.type(screen.getByLabelText('Per quanto'), '3 mesi{Enter}')
    await user.type(screen.getByLabelText('Budget a giornata'), '500{Enter}')
    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    expect(loadDraft(COMPANY_DRAFT_KEY)).toBeNull()
    const body = JSON.parse(fetchSpy.mock.calls[0]![1]?.body as string)
    expect(body.nome_azienda).toBe('ACME Srl')
    expect(body.distinct_id).toBe('anon-1')
  })
})
