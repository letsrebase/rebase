/**
 * The three tabs and the period control, driven entirely by the search params.
 *
 * The component takes the search object and an `onSearchChange` callback rather than
 * reaching for the router itself, so the round trip the URL exists for is assertable:
 * what comes in as search props is what the tab requests, and what the controls change is
 * handed back as a new search object for the route to push into the URL.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import type { DashboardSearch } from './search'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

// The tabs link out (the fiscal card points at Impostazioni → Fiscale when a parameter is
// missing), and a `<Link>` outside a router throws. What those destinations are is asserted
// in each tab's own file; here the only question is which tab got mounted.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const EMPTY_DASHBOARD = {
  periodo: { da: '2026-03-01', a: '2026-03-31' },
  calcolato_alle: '2026-03-15T10:00:00Z',
  pipeline: [],
  chiusure: { vinti: 0, persi: 0, valore_vinto: '0.00', tasso_conversione: null },
  offerte_in_attesa: [],
  offerte_in_attesa_totale: 0,
  chiusure_previste_30_giorni: 0,
  chiusure_non_attribuibili: 0,
  offerte_accettate_deal_non_vinto: 0,
}


const EMPTY_OVERVIEW = {
  calcolato_alle: '2026-03-15T10:00:00Z',
  cassa: {
    anno: 2026,
    incassato: '0.00',
    da_incassare: '0.00',
    bozze: '0.00',
    proiettato: '0.00',
    costi: '0.00',
    lordo_effettivo: '0.00',
    lordo_proiettato: '0.00',
    mesi: [],
    base: 'competenza',
  },
  fiscale: null,
  fiscale_proiettato: null,
  concentrazione_clienti: [],
  netto_effettivo: null,
  netto_proiettato: null,
}


const EMPTY_ESTIMATE = {
  anno: 2026,
  stima: true,
  avvertenza: 'Stima indicativa.',
  ricavi: '0.00',
  coefficiente_redditivita: null,
  imponibile: null,
  aliquota_imposta_sostitutiva: null,
  imposta_sostitutiva: null,
  aliquota_inps: null,
  contributi: null,
  reddito_netto_stimato: null,
}

/** One mock for every endpoint the tabs read: which tab is mounted decides which are
 *  called. The economic one reads two -- the overview behind its cards and the estimate
 *  behind the «Stima fiscale» card under them. */
const EMPTY_RECEIVABLES = {
  calcolato_alle: '2026-03-15T10:00:00Z',
  oggi: '2026-03-15',
  totale: '0.00',
  fasce: [],
  per_mese: [],
  per_cliente: [],
  scadute: [],
  scadute_totale: 0,
}

const BY_PATH: Record<string, unknown> = {
  '/api/dashboard/sales': EMPTY_DASHBOARD,
  '/api/dashboard/receivables': EMPTY_RECEIVABLES,
  '/api/analytics/overview': EMPTY_OVERVIEW,
  '/api/analytics/fiscal': EMPTY_ESTIMATE,
}

const SEARCH: DashboardSearch = {
  tab: 'commerciale',
  da: '2026-03-01',
  a: '2026-03-31',
  base: 'competenza',
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
    renderPage({ ...SEARCH, tab: 'economica' })
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
    renderPage({ tab: 'commerciale', da: '2024-06-01', a: '2024-06-30', base: 'competenza' })
    expect(await screen.findByText(/nessun dato nel periodo/i)).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/dashboard/sales', {
      params: { query: { da: '2024-06-01', a: '2024-06-30' } },
    })
  })

  it('hands a changed period back for the URL rather than keeping it in local state', async () => {
    const { onSearchChange } = renderPage()
    await userEvent.click(screen.getByRole('button', { name: 'Anno' }))
    expect(onSearchChange).toHaveBeenCalledTimes(1)
    const [next] = onSearchChange.mock.calls[0]!
    expect(next.da).toMatch(/^\d{4}-01-01$/)
    expect(next.a).toMatch(/^\d{4}-12-31$/)
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
    const { onSearchChange } = renderPage({ ...SEARCH, tab: 'economica' })
    await userEvent.click(screen.getByRole('tab', { name: 'Commerciale' }))
    expect(onSearchChange).toHaveBeenCalledWith({ tab: 'commerciale' })
  })

  it('passes the reading from the URL through to the economic request, and hands a change back', async () => {
    // ORB-133: the switch is the page's, the state is the URL's. Nothing is held locally,
    // so a shared link reopens on the same reading.
    const { onSearchChange } = renderPage({ ...SEARCH, tab: 'economica', base: 'incasso' })
    await screen.findByRole('group', { name: 'Ricavi incassati' })
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/overview', {
      params: { query: { anno: 2026, base: 'incasso' } },
    })
    await userEvent.click(screen.getByRole('radio', { name: 'Competenza' }))
    expect(onSearchChange).toHaveBeenCalledWith({ base: 'competenza' })
  })

  it('keeps a typed date in the URL too', () => {
    // `fireEvent`, not `userEvent.type`: an `<input type="date">` in jsdom does not accept
    // keystrokes the way a text input does, and a typing simulation would assert nothing.
    const { onSearchChange } = renderPage()
    fireEvent.change(screen.getByLabelText('Dal'), { target: { value: '2025-02-01' } })
    expect(onSearchChange).toHaveBeenLastCalledWith({ da: '2025-02-01', a: '2026-03-31' })
  })

})

describe('DashboardPage, the page intestazione', () => {
  /**
   * Since the 2026-09-08 revision the dashboard owns its own header rather than being
   * given one by the route: the tabs are part of the intestazione (§4, «Sotto, quando
   * servono, le tab»), and the period picker is this screen's one header control. A
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
})

describe('DashboardPage, three tabs', () => {
  it('offers the economic, commercial and scadenziario tabs, and marks the current one', () => {
    renderPage({ ...SEARCH, tab: 'economica' })
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    expect(tabs).toEqual(['Economica', 'Commerciale', 'Scadenziario'])
    expect(screen.getByRole('tab', { name: 'Economica' })).toHaveAttribute('aria-selected', 'true')
  })

  it('shows the period picker on the two tabs that have a period', () => {
    renderPage({ ...SEARCH, tab: 'economica' })
    expect(screen.getByLabelText('Dal')).toBeInTheDocument()
  })

  /** REB-329: the scadenziario has no period, so the picker would change nothing on
   *  screen; it is not drawn, and the tab reads its own endpoint and no other. */
  it('mounts the scadenziario without a period picker, reading its own endpoint', async () => {
    renderPage({ ...SEARCH, tab: 'scadenziario' })
    expect(await screen.findByText(/scadenziario per fascia/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Dal')).not.toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/dashboard/receivables')
    expect(api.GET).not.toHaveBeenCalledWith('/api/dashboard/sales', expect.anything())
  })
})
