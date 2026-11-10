/**
 * «Tasse» (REB-1026, the approved brief): the three readings of each tax, the reading that
 * counts in bold, the server's totals, the dates of the year, and every state the brief draws:
 * a missing parameter, no fiscal profile, a regime with no taxes, a frozen year, a pack with no
 * calendar and a role that may not read. Nothing is computed here: the page formats strings.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { spaceModules } from '@/lib/modules.testing'
import { queryKeys } from '@/lib/query'
import { TaxesPage } from './TaxesPage'
import type { TaxesTab } from './search'

const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) => can(session.role, action),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const WARNING = 'Stima indicativa. Per la dichiarazione fai riferimento al tuo commercialista.'

const line = (tax: string, overrides: Record<string, unknown> = {}) => ({
  tax,
  estimable: false,
  estimate: null,
  declared: null,
  paid: null,
  counts: null,
  ...overrides,
})

const estimate = (overrides: Record<string, unknown> = {}) => ({
  year: 2026,
  legal_entity_id: 'le-1',
  estimate: true,
  warning: WARNING,
  revenue: '40000.00',
  profitability_coefficient: '78.00',
  taxable_amount: '31200.00',
  substitute_tax_rate: '5.00',
  substitute_tax: '1560.00',
  social_security_rate: '26.07',
  contributions: '8133.84',
  estimated_net_income: '30000.00',
  due_total: '9693.84',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  law_from_year: 2015,
  frozen: false,
  reading: 'estimate',
  declared: [],
  readings: {
    lines: [
      line('substitute_tax', { estimable: true, estimate: '1560.00', counts: 'estimate' }),
      line('inps', { estimable: true, estimate: '8133.84', counts: 'estimate' }),
    ],
    totals: {
      estimate: '9693.84',
      estimate_left_out: 0,
      declared: null,
      declared_left_out: 2,
      paid: null,
      paid_left_out: 2,
    },
  },
  ...overrides,
})

const COMPANY = estimate({
  pack_id: 'it-company',
  law_from_year: 2026,
  substitute_tax: null,
  contributions: null,
  declared: [
    {
      reading: 'declared',
      legal_entity_id: 'le-1',
      tax: 'ires',
      year: 2026,
      tax_due: '12000.00',
      balance: '-150.00',
      advances: null,
      source: 'typed',
      updated_at: '2026-11-03T10:00:00Z',
    },
  ],
  readings: {
    lines: [
      line('ires', { declared: '12000.00', counts: 'declared' }),
      line('irap'),
    ],
    totals: {
      estimate: null,
      estimate_left_out: 2,
      declared: '12000.00',
      declared_left_out: 1,
      paid: null,
      paid_left_out: 2,
    },
  },
})

const CALENDAR = {
  year: 2026,
  legal_entity_id: 'le-1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  dates: [
    {
      nominal_date: '2026-06-30',
      due_date: '2026-06-30',
      tax: 'substitute_tax',
      kind: 'balance_and_first_advance',
      cadence: null,
      covers: 'x',
      source: 'Agenzia delle Entrate, Scadenzario fiscale',
    },
    {
      nominal_date: '2026-11-30',
      due_date: '2026-11-30',
      tax: 'substitute_tax',
      kind: 'second_advance',
      cadence: null,
      covers: 'x',
      source: 'Agenzia delle Entrate, Scadenzario fiscale',
    },
  ],
}

let answers: Record<string, unknown>

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(status: number, detail: string) {
  return Promise.resolve({
    error: { detail, status },
    response: new Response(null, { status }),
  }) as never
}

function renderPage(
  { tab = 'taxes', profile = 'company', year }: { tab?: TaxesTab; profile?: 'solo' | 'company'; year?: number } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(queryKeys.modules, spaceModules([], profile))
  return render(
    <QueryClientProvider client={client}>
      <TaxesPage tab={tab} year={year} onTabChange={vi.fn()} onYearChange={vi.fn()} />
    </QueryClientProvider>,
  )
}

function wide() {
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia
}

beforeEach(() => {
  session.role = 'admin'
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 9, 0, 0))
  answers = {
    '/api/analytics/fiscal': estimate(),
    '/api/analytics/fiscal/calendar': CALENDAR,
  }
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    const answer = answers[path]
    return answer instanceof Promise ? answer : ok(answer)
  }) as never)
  // The shell's default is the narrow layout: cards.
  Reflect.deleteProperty(window, 'matchMedia')
})
afterEach(() => vi.useRealTimers())

describe('«Tasse»: Imposte', () => {
  it('draws the server’s warning above the figures, then three readings per tax', async () => {
    answers['/api/analytics/fiscal'] = COMPANY
    wide()
    renderPage()
    const warning = await screen.findByText(WARNING)
    const table = await screen.findByRole('table')
    expect(warning.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
    expect(headers).toEqual(['Imposta', 'Stima', 'Dichiarato', 'Pagato'])
    const [ires, irap] = within(table).getAllByRole('row').slice(1, 3)
    // An estimate the code does not compute reads «non calcolabile», a declared figure is the typed one
    // in bold, a reading not there yet is a dash. Never a zero.
    expect(within(ires as HTMLElement).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'IRES',
      'non calcolabile',
      '12.000,00\u00a0€',
      '—',
    ])
    expect(within(irap as HTMLElement).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'IRAP',
      'non calcolabile',
      '—',
      '—',
    ])
    expect(within(ires as HTMLElement).getByText('12.000,00 €')).toHaveClass('font-semibold')
    expect(table.textContent).not.toMatch(/\b0,00/)
  })

  it('shows the server’s totals, says what a total leaves out and keeps the legend', async () => {
    answers['/api/analytics/fiscal'] = COMPANY
    wide()
    renderPage()
    const table = await screen.findByRole('table')
    const total = within(table).getAllByRole('row').at(-1) as HTMLElement
    expect(within(total).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'Totale',
      'non calcolabile',
      '12.000,00\u00a0€',
      '—',
    ])
    expect(screen.getByText('In grassetto la cifra che vale: pagato, poi dichiarato, poi stima.')).toBeInTheDocument()
    expect(screen.getByText('Il totale di «Dichiarato» non comprende 1 riga senza cifra.')).toBeInTheDocument()
    // No estimate total, so nothing is said to be left out of one.
    expect(screen.queryByText(/Il totale di «Stima»/)).not.toBeInTheDocument()
  })

  it('names the parameter’s place when an estimable tax has no estimate, and only then', async () => {
    answers['/api/analytics/fiscal'] = estimate({
      readings: {
        ...estimate().readings,
        lines: [line('substitute_tax', { estimable: true }), line('inps', { estimable: true })],
      },
    })
    renderPage()
    expect(
      await screen.findByText(
        'Alcune righe sono «non calcolabile» perché manca un parametro: imposta i parametri fiscali in Impostazioni.',
      ),
    ).toBeInTheDocument()
  })

  it('does not blame a parameter for a tax the code does not estimate', async () => {
    answers['/api/analytics/fiscal'] = COMPANY
    renderPage()
    await screen.findByText(WARNING)
    expect(screen.queryByText(/perché manca un parametro/)).not.toBeInTheDocument()
  })

  it('signs the figures with the pack, its version, its law and the regime', async () => {
    answers['/api/analytics/fiscal'] = COMPANY
    renderPage()
    expect(
      await screen.findByText(
        'Pacchetto fiscale it-company, versione 1, legge in vigore dal 2026 (società di capitali).',
      ),
    ).toBeInTheDocument()
  })

  it('opens a tax’s panel with its provenance and no action to take', async () => {
    answers['/api/analytics/fiscal'] = COMPANY
    wide()
    renderPage()
    await userEvent.click(await screen.findByRole('row', { name: /IRES/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByRole('heading', { name: 'IRES 2026' })).toBeInTheDocument()
    expect(within(panel).getByText('Vale: Dichiarato.')).toBeInTheDocument()
    expect(within(panel).getByText('-150,00 €')).toBeInTheDocument()
    expect(within(panel).getByText(/Digitato a mano il 03\/11\/2026/)).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: /Accetta/ })).not.toBeInTheDocument()
  })

  it('says a frozen year’s figures are as they were', async () => {
    answers['/api/analytics/fiscal'] = estimate({ frozen: true })
    renderPage()
    expect(
      await screen.findByText('Cifre dell’anno com’erano: il pacchetto attuale non le calcola più.'),
    ).toBeInTheDocument()
  })

  it('says what lives here when the year has no line', async () => {
    answers['/api/analytics/fiscal'] = estimate({
      readings: { lines: [], totals: estimate().readings.totals },
    })
    renderPage()
    expect(
      await screen.findByText('Qui compaiono le imposte e i contributi dell’anno, con stima, dichiarato e pagato.'),
    ).toBeInTheDocument()
  })

  it('has one sentence for a regime with no taxes, and no tabs', async () => {
    answers['/api/analytics/fiscal'] = estimate({ pack_id: 'non-it', readings: { lines: [], totals: estimate().readings.totals } })
    renderPage()
    expect(
      await screen.findByText('Per questo regime fiscale non si calcolano imposte: qui non c’è niente da mostrare.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('sends a legal entity with no fiscal profile to Settings', async () => {
    answers['/api/analytics/fiscal'] = failed(404, 'Profilo fiscale non trovato')
    renderPage()
    expect(await screen.findByText(/Profilo fiscale non configurato/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'imposta i parametri fiscali' })).toHaveAttribute(
      'href',
      '/app/settings/legal-entities',
    )
  })

  it('says a failed read with «Riprova» and no table under it', async () => {
    answers['/api/analytics/fiscal'] = failed(500, 'Il server non risponde.')
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('opens on «Imposte» with «Scadenze» beside it, and no «F24» tab', async () => {
    renderPage()
    await screen.findByText(WARNING)
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Imposte', 'Scadenze'])
    expect(screen.getByRole('tab', { name: 'Imposte' })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('«Tasse»: Scadenze', () => {
  it('lists the dates of the year with their distance, what falls due and the source line', async () => {
    wide()
    renderPage({ tab: 'dates' })
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('30/06/2026')
    expect(rows[0]).toHaveTextContent('133 giorni fa')
    expect(rows[0]).toHaveTextContent('Saldo 2025 e primo acconto 2026')
    expect(rows[1]).toHaveTextContent('30/11/2026')
    expect(rows[1]).toHaveTextContent('tra 20 giorni')
    expect(rows[1]).toHaveTextContent('non calcolabile')
    expect(screen.getByText('Le date sono quelle del pacchetto fiscale, salvo proroghe.')).toBeInTheDocument()
  })

  it('opens a date to its lines and their source', async () => {
    wide()
    renderPage({ tab: 'dates' })
    await userEvent.click(await screen.findByRole('row', { name: /30\/11\/2026/ }))
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).getByText('Secondo acconto 2026')).toBeInTheDocument()
    expect(within(panel).getByText(/Fonte: Agenzia delle Entrate/)).toBeInTheDocument()
  })

  it('says a pack with no calendar has none to show, and that it is no promise of nothing to pay', async () => {
    answers['/api/analytics/fiscal/calendar'] = { ...CALENDAR, dates: [] }
    renderPage({ tab: 'dates' })
    expect(
      await screen.findByText(
        'Il pacchetto fiscale di questo regime non porta ancora le date del 2026: qui non c’è niente da mostrare. Non vuol dire che non ci sia niente da pagare.',
      ),
    ).toBeInTheDocument()
  })

  it('shows more rows past twenty-five', async () => {
    const dates = Array.from({ length: 30 }, (_, index) => {
      const day = `2026-${String((index % 12) + 1).padStart(2, '0')}-${String(10 + Math.floor(index / 12)).padStart(2, '0')}`
      return { ...CALENDAR.dates[0], nominal_date: day, due_date: day }
    })
    answers['/api/analytics/fiscal/calendar'] = { ...CALENDAR, dates }
    wide()
    renderPage({ tab: 'dates' })
    const table = await screen.findByRole('table')
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(25)
    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(30)
  })
})

describe('«Tasse»: a solo flat-rate space', () => {
  it('has one light page: no tabs, a card per tax, the dates in a compact table', async () => {
    wide()
    renderPage({ profile: 'solo', tab: 'dates' })
    expect(await screen.findByRole('heading', { name: 'Le tue tasse' })).toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    expect(screen.getByText('Imposta sostitutiva')).toBeInTheDocument()
    expect(screen.getByText('1.560,00 €')).toHaveClass('font-semibold')
    expect(screen.getByRole('heading', { name: 'Scadenze' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Importo' })).not.toBeInTheDocument()
  })

  it('draws a section only when it has rows', async () => {
    answers['/api/analytics/fiscal/calendar'] = { ...CALENDAR, dates: [] }
    renderPage({ profile: 'solo' })
    await screen.findByRole('heading', { name: 'Le tue tasse' })
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(screen.queryByRole('heading', { name: 'Scadenze' })).not.toBeInTheDocument()
    expect(screen.queryByText(/non porta ancora le date/)).not.toBeInTheDocument()
  })

  it('keeps the tabs for an ordinary solo', async () => {
    answers['/api/analytics/fiscal'] = estimate({ pack_id: 'it-standard', law_from_year: 2026 })
    renderPage({ profile: 'solo' })
    await screen.findByText(WARNING)
    expect(screen.getAllByRole('tab')).toHaveLength(2)
  })
})

describe('«Tasse»: who may read', () => {
  it('draws one sentence for a collaborator and asks for no figure', async () => {
    session.role = 'collaborator'
    renderPage()
    expect(
      await screen.findByText('Il tuo ruolo in questo spazio è «collaboratore»: le tasse sono riservate all’«amministratore».'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Anno d’imposta' })).not.toBeInTheDocument()
    expect(vi.mocked(api.GET)).not.toHaveBeenCalled()
  })

  it('offers the tax year to an administrator, the current one chosen', async () => {
    renderPage()
    expect(await screen.findByRole('combobox', { name: 'Anno d’imposta' })).toHaveTextContent('2026')
  })

  it('reads the year the URL names', async () => {
    renderPage({ year: 2025 })
    await screen.findByText(WARNING)
    expect(vi.mocked(api.GET)).toHaveBeenCalledWith('/api/analytics/fiscal', {
      params: { query: { year: 2025 } },
    })
  })
})
