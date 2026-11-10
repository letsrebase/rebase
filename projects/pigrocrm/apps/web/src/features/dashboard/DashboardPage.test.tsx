/**
 * The four tabs and the period control, driven entirely by the search params.
 *
 * The component takes the search object and an `onSearchChange` callback rather than
 * reaching for the router itself, so the round trip the URL exists for is assertable:
 * what comes in as search props is what the tab requests, and what the controls change is
 * handed back as a new search object for the route to push into the URL.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// REB-788: the panels read the role (the estimate is an administrator's), so the tests
// say which role is signed in; the default is the one that may read everything.
const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) => can(session.role, action),
  }
})
import { DashboardPage } from './DashboardPage'
import type { DashboardSearch } from './search'
import { api } from '@/lib/api'
import { spaceModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

// The tabs link out (the fiscal card points at Settings → Legal entities when a parameter is
// missing), and a `<Link>` outside a router throws. What those destinations are is asserted
// in each tab's own file; here the only question is which tab got mounted.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const EMPTY_DASHBOARD = {
  period: { from: '2026-03-01', to: '2026-03-31' },
  calculated_at: '2026-03-15T10:00:00Z',
  pipeline: [],
  closings: {
    won: 0,
    lost: 0,
    won_value: '0.00',
    won_monthly_recurring: '0.00',
    conversion_rate: null,
  },
  open_monthly_recurring: '0.00',
  offers_pending: [],
  offers_pending_total: 0,
  expected_closings_30_days: 0,
  closings_not_attributable: 0,
  accepted_offers_deal_not_won: 0,
}


const EMPTY_OVERVIEW = {
  calculated_at: '2026-03-15T10:00:00Z',
  pension_fund: {
    year: 2026,
    paid: '0.00',
    unpaid: '0.00',
    drafts: '0.00',
    projected: '0.00',
    costs: '0.00',
    actual_gross: '0.00',
    projected_gross: '0.00',
    months: [],
    base: 'accrual',
  },
  tax: null,
  projected_tax: null,
  customer_concentration: [],
  actual_net: null,
  projected_net: null,
}


const EMPTY_ESTIMATE = {
  year: 2026,
  estimate: true,
  warning: 'Stima indicativa.',
  revenue: '0.00',
  profitability_coefficient: null,
  taxable_amount: null,
  substitute_tax_rate: null,
  substitute_tax: null,
  social_security_rate: null,
  contributions: null,
  estimated_net_income: null,
}

/** One mock for every endpoint the tabs read: which tab is mounted decides which are
 *  called. The economic one reads two -- the overview behind its cards and the estimate
 *  behind the «Stima fiscale» card under them. */
const EMPTY_RECEIVABLES = {
  calculated_at: '2026-03-15T10:00:00Z',
  today: '2026-03-15',
  total: '0.00',
  bands: [],
  by_month: [],
  by_customer: [],
  overdue: [],
  total_overdue: 0,
}

const EMPTY_YEAR = {
  year: 2026,
  legal_entity_id: 'a1',
  legal_entity_name: 'Studio Rossi',
  total: { invoices: 0, taxable_amount: '0.00', social_security_surcharge: '0.00', stamp_duty: '0.00', total: '0.00' },
  regimes: [],
  treatments: [],
  register: [],
  cancelled: 0,
}

/** A company space with nothing open: the schedule says so in one sentence. */
const EMPTY_SCHEDULE = {
  calculated_at: '2026-03-15T10:00:00Z',
  today: '2026-03-15',
  legal_entity_id: null,
  horizon_weeks: 13,
  horizon_options: [4, 13, 26],
  profile: 'company',
  cash_enabled: true,
  currency: 'EUR',
  show_outgoing: true,
  show_balance: false,
  accounts: null,
  total_balance: null,
  late_receivable: { count: 0, total: '0.00', oldest_days: null },
  late_payable: { count: 0, total: '0.00', oldest_days: null },
  weeks: [],
  taxes: null,
  alerts: [],
  has_open_items: false,
}

const BY_PATH: Record<string, unknown> = {
  '/api/dashboard/sales': EMPTY_DASHBOARD,
  '/api/dashboard/receivables': EMPTY_RECEIVABLES,
  '/api/cash/schedule': EMPTY_SCHEDULE,
  '/api/cash/payables': EMPTY_RECEIVABLES,
  '/api/analytics/overview': EMPTY_OVERVIEW,
  '/api/analytics/fiscal': EMPTY_ESTIMATE,
  // No ceiling applies: the panel under the figures draws nothing (REB-755).
  '/api/analytics/ceilings': { year: 2026, legal_entity_id: 'a1', pack_id: 'it-flat-rate', pack_version: '1', thresholds: [] },
  // No active contract caps a customer's share: nor does that panel have anything to show.
  '/api/contracts': { items: [], next_cursor: null },
  '/api/analytics/year': EMPTY_YEAR,
  // The pack's dates (REB-1026): the next two ride in a band under «Da fare», on every tab.
  '/api/analytics/fiscal/calendar': {
    year: new Date().getFullYear(),
    legal_entity_id: 'a1',
    pack_id: 'it-standard',
    pack_version: '1',
    dates: ['06-30', '11-30'].map((day) => ({
      nominal_date: `${new Date().getFullYear() + 1}-${day}`,
      due_date: `${new Date().getFullYear() + 1}-${day}`,
      tax: 'income_tax',
      kind: 'second_advance',
      cadence: null,
      covers: 'x',
      source: 'Agenzia delle Entrate',
    })),
  },
  // Nothing late and nothing due today: the «Da fare» band is its one line (REB-874, and
  // `DaFareBand.test.tsx` for the rest).
  '/api/activities/today': {
    today: '2026-03-15',
    overdue: { total: 0, items: [] },
    today_due: { total: 0, items: [] },
    next_seven_days: { total: 0, items: [] },
    without_due_date: { total: 0, items: [] },
  },
}

const SEARCH: DashboardSearch = {
  tab: 'commercial',
  from: '2026-03-01',
  to: '2026-03-31',
  base: 'accrual',
}

function renderPage(search = SEARCH) {
  const onSearchChange = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <DashboardPage search={search} onSearchChange={onSearchChange} />
    </QueryClientProvider>,
  )
  return { onSearchChange }
}

beforeEach(() => {
  session.role = 'admin'
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockImplementation(
    ((path: string) =>
      Promise.resolve({
        data: BY_PATH[path],
        response: new Response(null, { status: 200 }),
      })) as never,
  )
})

describe('DashboardPage', () => {
  it('renders the tab the URL names, not the first one', async () => {
    renderPage({ ...SEARCH, tab: 'economic' })
    expect(await screen.findByRole('group', { name: 'Ricavi incassati' })).toBeInTheDocument()
    // The commercial tab's own card, by the title it has today: after the 2026-09-09
    // rename «Pipeline aperta per stato» exists nowhere, so asserting *its* absence
    // passed whether or not CommercialTab was mounted. Both are checked, so neither the
    // guard nor the rename can go quiet.
    expect(screen.queryByText(/pipeline per stato/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/pipeline aperta per stato/i)).not.toBeInTheDocument()
    // One tab at a time: rendering both and hiding one would open the other's snapshot
    // transactions to draw a screen nobody is looking at. Asserted by which endpoints were
    // read, not by a call count -- the economic tab legitimately reads two of them, the
    // overview behind its cards and the estimate behind the «Stima fiscale» card.
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/overview', expect.anything())
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/fiscal', expect.anything())
    expect(api.GET).not.toHaveBeenCalledWith('/api/dashboard/sales', expect.anything())
  })

  it('passes the period from the URL through to the request', async () => {
    renderPage({ tab: 'commercial', from: '2024-06-01', to: '2024-06-30', base: 'accrual' })
    expect(await screen.findByText(/nessun dato nel periodo/i)).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/dashboard/sales', {
      params: { query: { from: '2024-06-01', to: '2024-06-30' } },
    })
  })

  it('hands a changed period back for the URL rather than keeping it in local state', async () => {
    const { onSearchChange } = renderPage()
    await userEvent.click(screen.getByRole('button', { name: 'Anno' }))
    expect(onSearchChange).toHaveBeenCalledTimes(1)
    const [next] = onSearchChange.mock.calls[0]!
    expect(next.from).toMatch(/^\d{4}-01-01$/)
    expect(next.to).toMatch(/^\d{4}-12-31$/)
  })

  it('draws the three period presets as controls and not as bare text', () => {
    // What the 1440 screenshot of the 2026-09-08 visual pass caught: as `variant="ghost"`
    // buttons, «Mese / Trimestre / Anno» had neither line nor fill, so the one cluster in
    // the top right of Home read as a caption. Spec §4 gives the controls of a filter
    // row a line on the white panel; since REB-299 that line is 1px of the ink itself,
    // which is what `border-border` resolves to.
    renderPage()
    for (const label of ['Mese', 'Trimestre', 'Anno']) {
      expect(screen.getByRole('button', { name: label }).className, label).toContain(
        'border-border',
      )
    }
  })

  it('lets the two dates shrink, so the period never reaches past the panel', () => {
    // At 390 «Dal 01/09/2026 al 30/09/2026» ran off the right edge of the panel
    // (`home-390.png`): the row already wrapped, but a date input will not go below its
    // intrinsic width without being told it may.
    renderPage()
    for (const label of ['Dal', 'al']) {
      expect(screen.getByLabelText(label).className, label).toContain('min-w-0')
    }
  })

  it('hands a changed tab back for the URL', async () => {
    const { onSearchChange } = renderPage({ ...SEARCH, tab: 'economic' })
    await userEvent.click(screen.getByRole('tab', { name: 'Commerciale' }))
    expect(onSearchChange).toHaveBeenCalledWith({ tab: 'commercial' })
  })

  it('passes the reading from the URL through to the economic request, and hands a change back', async () => {
    // ORB-133: the switch is the page's, the state is the URL's. Nothing is held locally,
    // so a shared link reopens on the same reading.
    const { onSearchChange } = renderPage({ ...SEARCH, tab: 'economic', base: 'collection' })
    await screen.findByRole('group', { name: 'Ricavi incassati' })
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/overview', {
      params: { query: { year: 2026, base: 'collection' } },
    })
    await userEvent.click(screen.getByRole('radio', { name: 'Competenza' }))
    expect(onSearchChange).toHaveBeenCalledWith({ base: 'accrual' })
  })

  it('keeps a typed date in the URL too', () => {
    // `fireEvent`, not `userEvent.type`: an `<input type="date">` in jsdom does not accept
    // keystrokes the way a text input does, and a typing simulation would assert nothing.
    const { onSearchChange } = renderPage()
    fireEvent.change(screen.getByLabelText('Dal'), { target: { value: '2025-02-01' } })
    expect(onSearchChange).toHaveBeenLastCalledWith({ from: '2025-02-01', to: '2026-03-31' })
  })

})

describe('DashboardPage, the page intestazione', () => {
  /**
   * Since the 2026-09-08 revision the dashboard owns its own header rather than being
   * given one by the route: the tabs are part of the intestazione (§4: the tabs sit under it, when there are any), and the period picker is this screen's one header control. A
   * route that kept drawing its own `PageHeader` above this one would put two `<h1>`s
   * on the home page.
   */
  it('draws its own header, with the tabs and the period under it', () => {
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Home' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('tab', { name: 'Commerciale' })).toBeInTheDocument()
    expect(screen.getByLabelText('Dal')).toBeInTheDocument()
  })

  /** REB-874: the band sits between the title and the tab strip, so it is the first thing
   *  under «Home» and the analysis tabs start below it. */
  it('draws the «Da fare» band above the analysis tabs', async () => {
    renderPage()
    const line = await screen.findByText('Niente in ritardo né in scadenza oggi.')
    const tab = screen.getByRole('tab', { name: 'Commerciale' })
    expect(line.compareDocumentPosition(tab) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const title = screen.getByRole('heading', { level: 1, name: 'Home' })
    expect(title.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  /** REB-1026: the next tax dates are a band of their own under «Da fare» and above the tabs,
   *  on every tab and not inside one. */
  it('draws the next tax dates between «Da fare» and the tabs, whichever tab is open', async () => {
    renderPage({ ...SEARCH, tab: 'receivables' })
    const band = await screen.findByRole('heading', { name: 'Prossime scadenze fiscali' })
    const daFare = await screen.findByText('Niente in ritardo né in scadenza oggi.')
    const tab = screen.getByRole('tab', { name: 'Scadenziario' })
    expect(daFare.compareDocumentPosition(band) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(band.compareDocumentPosition(tab) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(await screen.findAllByText('Secondo acconto ' + (new Date().getFullYear() + 1) + ' · Imposte sui redditi')).toHaveLength(2)
  })

  it('draws no tax dates and asks for none for a collaborator', async () => {
    session.role = 'collaborator'
    renderPage()
    await screen.findByText('Niente in ritardo né in scadenza oggi.')
    expect(screen.queryByRole('heading', { name: 'Prossime scadenze fiscali' })).toBeNull()
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/fiscal/calendar', expect.anything())
  })
})

describe('DashboardPage, four tabs', () => {
  it('offers the economic, commercial, scadenziario, anno and giornate tabs, and marks the current one', () => {
    renderPage({ ...SEARCH, tab: 'economic' })
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    expect(tabs).toEqual(['Economica', 'Commerciale', 'Scadenziario', 'Anno', 'Giornate'])
    expect(screen.getByRole('tab', { name: 'Economica' })).toHaveAttribute('aria-selected', 'true')
  })

  /** REB-757: the year tab reads the year the period names and nothing else. */
  it('mounts the anno tab on the picker\'s year, with the picker', async () => {
    renderPage({ ...SEARCH, tab: 'year' })
    expect(await screen.findByText(/nessuna fattura emessa nel 2026/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Dal')).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/year', { params: { query: { year: 2026 } } })
  })

  it('shows the period picker on the two tabs that have a period', () => {
    renderPage({ ...SEARCH, tab: 'economic' })
    expect(screen.getByLabelText('Dal')).toBeInTheDocument()
  })

  /** REB-329, REB-1025: the scadenziario has no period, so the picker would change nothing on
   *  screen; it is not drawn. Its body is the cash schedule, which reads its own endpoint. */
  it('mounts the scadenziario without a period picker, as the cash schedule', async () => {
    renderPage({ ...SEARCH, tab: 'receivables' })
    expect(await screen.findByText('Niente da incassare né da pagare nelle prossime 13 settimane.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Dal')).not.toBeInTheDocument()
    // No period, and in «tutte» no azienda either: an empty query (REB-632).
    expect(api.GET).toHaveBeenCalledWith('/api/cash/schedule', { params: { query: {} } })
    expect(api.GET).not.toHaveBeenCalledWith('/api/dashboard/sales', expect.anything())
  })
})

/** REB-953: the tabs of a module the space has switched off are not drawn. */
describe('DashboardPage with modules off', () => {
  function answerModules(off: readonly ModuleKey[]) {
    vi.mocked(api.GET).mockImplementation(
      ((path: string) =>
        Promise.resolve({
          data: path === '/api/settings/modules' ? spaceModules(off) : BY_PATH[path],
          response: new Response(null, { status: 200 }),
        })) as never,
    )
  }

  it('draws no «Commerciale» tab with deals off, and opens a link to it on the first tab', async () => {
    answerModules(['deals'])
    renderPage({ ...SEARCH, tab: 'commercial' })
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Commerciale' })).toBeNull())
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Economica',
      'Scadenziario',
      'Anno',
      'Giornate',
    ])
    expect(screen.getByRole('tab', { name: 'Economica' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('group', { name: 'Ricavi incassati' })).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/dashboard/sales', expect.anything())
  })

  it('draws no «Anno» tab with taxes off and no «Giornate» with contracts off', async () => {
    answerModules(['taxes', 'contracts'])
    renderPage({ ...SEARCH, tab: 'year' })
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Anno' })).toBeNull())
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Economica',
      'Commerciale',
      'Scadenziario',
    ])
    expect(await screen.findByRole('group', { name: 'Ricavi incassati' })).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/year', expect.anything())
  })

  it('leaves the economic tab without its fiscal estimate and ceilings with taxes off', async () => {
    answerModules(['taxes'])
    renderPage({ ...SEARCH, tab: 'economic' })
    expect(await screen.findByRole('group', { name: 'Ricavi incassati' })).toBeInTheDocument()
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/settings/modules'))
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Anno' })).toBeNull())
    expect(screen.queryByText('Stima fiscale')).toBeNull()
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/fiscal', expect.anything())
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/ceilings', expect.anything())
  })

  it('draws every tab again with the modules on', async () => {
    answerModules([])
    renderPage({ ...SEARCH, tab: 'economic' })
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/settings/modules'))
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Economica',
      'Commerciale',
      'Scadenziario',
      'Anno',
      'Giornate',
    ])
  })
})
