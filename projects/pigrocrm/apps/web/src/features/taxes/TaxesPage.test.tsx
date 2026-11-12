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
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
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
  advance_method: 'historical',
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

const ONE: LegalEntityValue = {
  legal_entities: [],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  loaded: true,
  byId: () => undefined,
}

function renderPage(
  {
    tab = 'taxes',
    profile = 'company',
    year,
    legal = ONE,
    retry = false,
  }: {
    tab?: TaxesTab
    profile?: 'solo' | 'company'
    year?: number
    legal?: LegalEntityValue
    retry?: boolean
  } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: retry ? { retryDelay: 0 } : { retry: false } },
  })
  client.setQueryData(queryKeys.modules, spaceModules([], profile))
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext.Provider value={legal}>
        <TaxesPage tab={tab} year={year} onTabChange={vi.fn()} onYearChange={vi.fn()} />
      </LegalEntityContext.Provider>
    </QueryClientProvider>,
  )
}

const entityRecord = (id: string, name: string, day: number, month: number) =>
  ({ id, name, fiscal_year_end_day: day, fiscal_year_end_month: month }) as unknown as LegalEntityValue['legal_entities'][number]

/** The requests the page made to one path, with their query. */
function requests(path: string) {
  const calls = vi.mocked(api.GET).mock.calls as unknown as Array<
    [string, { params: { query: { year: number; legal_entity_id?: string } } }]
  >
  return calls.filter(([called]) => called === path).map(([, options]) => options.params.query)
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
    '/api/due-items': { items: [], next_cursor: null },
    '/api/f24-groups': { items: [], next_cursor: null },
    '/api/analytics/fiscal/plans': { legal_entity_id: 'le-1', instalment_plans: [], notice_plans: [] },
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

  it('names the parameters the estimate says are missing', async () => {
    answers['/api/analytics/fiscal'] = estimate({
      missing_parameters: ['profitability_coefficient', 'social_security_rate'],
      readings: {
        ...estimate().readings,
        lines: [line('substitute_tax', { estimable: true })],
      },
    })
    renderPage()
    expect(
      await screen.findByText(
        'Alcune righe sono «non calcolabile» perché manca: coefficiente di redditività, aliquota INPS. Compilali nei parametri fiscali, in Impostazioni.',
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

  it('draws an srl’s IRES and IRAP estimates and what each was computed from (REB-1073)', async () => {
    const tax = (amount: string | null, base: string, rate: string | null, lines: [string, string, string][]) => ({
      lines: lines.map(([code, label, value]) => ({ code, label, amount: value })),
      base,
      rate,
      amount,
      loss: false,
      region: '03',
      missing: null,
      assumptions: ['Una semplificazione dichiarata.'],
    })
    answers['/api/analytics/fiscal'] = {
      ...COMPANY,
      declared: [],
      basis: {
        label: 'Ricavi e costi di competenza del 2026',
        wording: 'quello che è maturato nell’anno',
        period_from: '2026-01-01',
        period_to: '2026-12-31',
        assumptions: [],
        left_out: [],
      },
      company: {
        ires: tax('8832.00', '36800.00', '24.00', [
          ['revenue', 'Ricavi di competenza', '100000.00'],
          ['purchases', 'Acquisti', '-20000.00'],
        ]),
        irap: tax('2808.00', '72000.00', '3.90', [
          ['revenue', 'Ricavi di competenza', '100000.00'],
          ['small_base', 'Deduzione per basi imponibili ridotte (art. 11 c. 4-bis)', '-8000.00'],
        ]),
      },
      readings: {
        lines: [
          line('ires', { estimable: true, estimate: '8832.00', counts: 'estimate' }),
          line('irap', { estimable: true, estimate: '2808.00', counts: 'estimate' }),
        ],
        totals: {
          estimate: '11640.00',
          estimate_left_out: 0,
          declared: null,
          declared_left_out: 2,
          paid: null,
          paid_left_out: 2,
        },
      },
    } as never
    wide()
    renderPage()
    await userEvent.click(await screen.findByRole('row', { name: /IRAP/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByText('Come è calcolata la stima')).toBeInTheDocument()
    expect(within(panel).getByText('Ricavi e costi di competenza del 2026.')).toBeInTheDocument()
    expect(within(panel).getByText('Deduzione per basi imponibili ridotte (art. 11 c. 4-bis)')).toBeInTheDocument()
    expect(within(panel).getByText('72.000,00 €')).toBeInTheDocument()
    expect(within(panel).getByText('3,90 %')).toBeInTheDocument()
    expect(within(panel).getByText('Una semplificazione dichiarata.')).toBeInTheDocument()
  })

  it('draws an srl’s IRAP split between regions as a table and never a null rate (REB-1092)', async () => {
    const part = (region: string, name: string, pay: string, share: string, base: string, rate: string, amount: string) => ({
      region,
      name,
      pay,
      share,
      base,
      rate,
      amount,
    })
    answers['/api/analytics/fiscal'] = {
      ...COMPANY,
      declared: [],
      company: {
        ires: { lines: [], base: '0.00', rate: '24.00', amount: '0.00', loss: false, missing: null, assumptions: [] },
        irap: {
          lines: [],
          base: '70000.00',
          rate: null,
          region: null,
          amount: '2870.00',
          loss: false,
          missing: null,
          assumptions: [],
          regions: [
            part('05', 'Veneto', '30000.00', '60.00', '42000.00', '3.90', '1638.00'),
            part('03', 'Lombardia', '20000.00', '40.00', '28000.00', '4.40', '1232.00'),
          ],
        },
      },
      readings: {
        lines: [
          line('ires', { estimable: true, estimate: '0.00', counts: 'estimate' }),
          line('irap', { estimable: true, estimate: '2870.00', counts: 'estimate' }),
        ],
        totals: { estimate: '2870.00', estimate_left_out: 0, declared: null, declared_left_out: 2, paid: null, paid_left_out: 2 },
      },
    } as never
    wide()
    renderPage()
    await userEvent.click(await screen.findByRole('row', { name: /IRAP/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByText(/Ripartita su 2 regioni/)).toBeInTheDocument()
    const table = within(panel).getByText('Veneto').closest('table') as HTMLElement
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent?.replace(/\u00a0/g, ' ')))).toEqual([
      ['Veneto', '60,00 %', '42.000,00 €', '3,90 %', '1.638,00 €'],
      ['Lombardia', '40,00 %', '28.000,00 €', '4,40 %', '1.232,00 €'],
    ])
    expect(within(panel).queryByText('non calcolabile')).not.toBeInTheDocument()
    expect(within(panel).queryByText('Aliquota', { selector: 'dt' })).not.toBeInTheDocument()
  })

  it('says which address an srl lacks when IRAP cannot be estimated', async () => {
    const missing = 'Manca l’indirizzo della sede dell’azienda.'
    answers['/api/analytics/fiscal'] = {
      ...COMPANY,
      declared: [],
      company: {
        ires: { lines: [], base: '0.00', rate: '24.00', amount: '0.00', loss: false, missing: null, assumptions: [] },
        irap: { lines: [], base: '0.00', rate: null, amount: null, loss: false, missing, assumptions: [] },
      },
      readings: {
        lines: [
          line('ires', { estimable: true, estimate: '0.00', counts: 'estimate' }),
          line('irap', { estimable: true }),
        ],
        totals: { estimate: '0.00', estimate_left_out: 1, declared: null, declared_left_out: 2, paid: null, paid_left_out: 2 },
      },
    } as never
    renderPage()
    expect(await screen.findByText(missing)).toBeInTheDocument()
    expect(screen.queryByText(/perché manca un parametro/)).not.toBeInTheDocument()
  })

  it('draws the fiscal year end of the entity the figures are for, not the first one listed', async () => {
    answers['/api/analytics/fiscal'] = { ...COMPANY, legal_entity_id: 'le-2' }
    wide()
    const entities = [entityRecord('le-1', 'Rossi', 31, 12), entityRecord('le-2', 'Bianchi', 30, 6)]
    renderPage({
      legal: {
        ...ONE,
        legal_entities: entities,
        byId: (id) => entities.find((candidate) => candidate.id === id),
      },
    })
    await userEvent.click(await screen.findByRole('row', { name: /IRES/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByText('Fine dell’esercizio: 30/06.')).toBeInTheDocument()
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

  it('draws one card per tax and no table on a phone, and opens a tax full screen', async () => {
    answers['/api/analytics/fiscal'] = COMPANY
    renderPage()
    expect(await screen.findByRole('list', { name: 'Imposte dell’anno' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /IRES/ }))
    const sheet = await screen.findByRole('dialog', { name: 'Imposta aperta' })
    expect(within(sheet).getByRole('heading', { name: 'IRES 2026' })).toBeInTheDocument()
  })

  it('asks no second time for a legal entity that has no fiscal profile', async () => {
    answers['/api/analytics/fiscal'] = failed(404, 'Profilo fiscale non trovato')
    renderPage({ retry: true })
    expect(await screen.findByText(/Profilo fiscale non configurato/)).toBeInTheDocument()
    expect(requests('/api/analytics/fiscal')).toHaveLength(1)
  })

  it('opens on «Imposte» with «Scadenze» and «Rate» beside it, and no «F24» tab', async () => {
    renderPage()
    await screen.findByText(WARNING)
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Imposte', 'Scadenze', 'Rate'])
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
    expect(screen.getAllByRole('tab')).toHaveLength(3)
  })
})

describe('«Tasse»: Tutte le aziende', () => {
  it('draws one block per legal entity under its name and sums none of them', async () => {
    answers['/api/analytics/fiscal'] = COMPANY
    renderPage({
      legal: {
        ...ONE,
        several: true,
        legal_entities: [entityRecord('le-1', 'Rossi', 31, 12), entityRecord('le-2', 'Bianchi', 31, 12)],
      },
    })
    expect(await screen.findByRole('heading', { name: 'Rossi' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Bianchi' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText('Totale')).toHaveLength(2))
    const asked = requests('/api/analytics/fiscal').map((query) => query.legal_entity_id)
    expect(asked.sort()).toEqual(['le-1', 'le-2'])
  })
})

const step = (code: string, label: string, amount: string | null) => ({ code, label, amount })

/** An ordinary person in Albinea who collected 65.789,47 euro: INPS 15.789,47, an IRPEF base of
 *  50.000, the Agenzia's 13.700 plus the two surcharges (REB-1072). */
const PERSON_ESTIMATE = {
    income: {
      lines: [
        step('revenue', 'Incassato nell’anno', '65789.47'),
        step('costs', 'Spese pagate nell’anno', '0.00'),
        step('inps_deducted', 'Contributi INPS dedotti (art. 10 c. 1 lett. e) TUIR)', '-15789.47'),
      ],
      professional_income: '65789.47',
      taxable_income: '50000.00',
      loss: false,
      assumptions: ['Non ci sono altri redditi, né altri oneri deducibili.'],
      sources: ['D.P.R. 917/1986 (TUIR), art. 10 c. 1 lett. e), Normattiva'],
    },
    inps: {
      lines: [step('income', 'Reddito dell’anno', '65789.47')],
      base: '65789.47',
      rate: '24.00',
      parts: [step('percentage', 'Contributi sul reddito', '15789.47')],
      amount: '15789.47',
      credit: null,
      region: null,
      cadastral_code: null,
      missing: [],
      assumptions: ['Le contributi sono calcolate sul reddito dell’anno.'],
      sources: ['INPS, circolare n. 8 of 3 February 2026'],
    },
    irpef: {
      lines: [],
      base: '50000.00',
      rate: null,
      parts: [
        step('gross_irpef', 'IRPEF lorda', '13700.00'),
        step('regional_surcharge', 'Addizionale regionale (Emilia-Romagna)', '1062.00'),
        step('municipal_surcharge', 'Addizionale comunale (Albinea)', '308.00'),
        step('withholdings', 'Ritenute subite sulle fatture incassate', '0.00'),
      ],
      amount: '13700.00',
      with_surcharges: '15070.00',
      credit: '0.00',
      region: '08',
      cadastral_code: 'A162',
      missing: [],
      assumptions: ['Le detrazioni dell’articolo 13 del TUIR non sono applicate: l’imposta è per eccesso.'],
      sources: ['Agenzia delle Entrate, «Aliquote e calcolo dell’Irpef», updated 13 January 2026'],
    },
    missing_parameters: [],
    due_total: '30859.47',
}

const PERSON = estimate({
  pack_id: 'it-standard',
  law_from_year: 2026,
  substitute_tax: null,
  taxable_amount: null,
  contributions: '15789.47',
  due_total: '30859.47',
  basis: {
    label: 'Incassato nel 2026, secondo il principio di cassa',
    wording: 'quello che è stato incassato',
    period_from: '2026-01-01',
    period_to: '2026-12-31',
    assumptions: [],
    left_out: [],
  },
  person: PERSON_ESTIMATE,
  readings: {
    lines: [
      line('irpef', { estimable: true, estimate: '13700.00', counts: 'estimate' }),
      line('inps', { estimable: true, estimate: '15789.47', counts: 'estimate' }),
    ],
    totals: {
      estimate: '29489.47',
      estimate_left_out: 0,
      declared: null,
      declared_left_out: 2,
      paid: null,
      paid_left_out: 2,
    },
  },
})

describe('«Tasse»: an ordinary person', () => {
  it('draws IRPEF with its surcharges and what each was computed from (REB-1072)', async () => {
    answers['/api/analytics/fiscal'] = PERSON
    wide()
    renderPage({ profile: 'solo' })
    const table = await screen.findByRole('table')
    const [irpef, inps] = within(table).getAllByRole('row').slice(1, 3)
    expect(within(irpef as HTMLElement).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'IRPEF',
      '13.700,00\u00a0€',
      '—',
      '—',
    ])
    expect(within(inps as HTMLElement).getAllByRole('cell')[1]).toHaveTextContent('15.789,47')

    await userEvent.click(irpef as HTMLElement)
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByText('Come è calcolata la stima')).toBeInTheDocument()
    expect(within(panel).getByText('Incassato nel 2026, secondo il principio di cassa.')).toBeInTheDocument()
    expect(within(panel).getByText('Contributi INPS dedotti (art. 10 c. 1 lett. e) TUIR)')).toBeInTheDocument()
    expect(within(panel).getByText('50.000,00 €')).toBeInTheDocument()
    expect(within(panel).getByText('IRPEF lorda')).toBeInTheDocument()
    expect(within(panel).getAllByText('13.700,00 €').length).toBeGreaterThanOrEqual(2)
    expect(within(panel).getByText('Addizionale regionale (Emilia-Romagna)')).toBeInTheDocument()
    expect(within(panel).getByText('1.062,00 €')).toBeInTheDocument()
    expect(within(panel).getByText('Addizionale comunale (Albinea)')).toBeInTheDocument()
    expect(within(panel).getByText('308,00 €')).toBeInTheDocument()
    expect(within(panel).getByText('IRPEF stimata')).toBeInTheDocument()
    expect(within(panel).getByText('IRPEF con le addizionali')).toBeInTheDocument()
    expect(within(panel).getByText('15.070,00 €')).toBeInTheDocument()
    expect(within(panel).getByText(/per eccesso/)).toBeInTheDocument()
    expect(within(panel).getByText(/art\. 10 c\. 1 lett\. e\), Normattiva/)).toBeInTheDocument()
  })

  it('draws the INPS lines, base, rate and the contributions', async () => {
    answers['/api/analytics/fiscal'] = PERSON
    wide()
    renderPage({ profile: 'solo' })
    await userEvent.click(await screen.findByRole('row', { name: /Contributi INPS/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByText('Base dei contributi')).toBeInTheDocument()
    expect(within(panel).getByText('24,00 %')).toBeInTheDocument()
    expect(within(panel).getByText('Contributi stimati')).toBeInTheDocument()
    expect(within(panel).getByText('Le contributi sono calcolate sul reddito dell’anno.')).toBeInTheDocument()
  })

  it('says why IRPEF has no figure, in the server’s words, instead of the generic sentence', async () => {
    const pending =
      'L’aliquota 2026 dell’addizionale comunale di Milano non è ancora pubblicata: l’elenco della Finanze letto il 2026-11-11 non ha la delibera.'
    answers['/api/analytics/fiscal'] = {
      ...PERSON,
      due_total: null,
      person: {
        ...PERSON_ESTIMATE,
        due_total: null,
        irpef: {
          ...PERSON_ESTIMATE.irpef,
          with_surcharges: null,
          missing: [pending],
          parts: [
            step('gross_irpef', 'IRPEF lorda', '13700.00'),
            step('regional_surcharge', 'Addizionale regionale (Lombardia)', '768.30'),
            step('municipal_surcharge', 'Addizionale comunale (Milano)', null),
          ],
        },
      },
      readings: {
        ...PERSON.readings,
        lines: [line('irpef', { estimable: true }), line('inps', { estimable: true, estimate: '15789.47', counts: 'estimate' })],
      },
    }
    wide()
    renderPage({ profile: 'solo' })
    expect(await screen.findByText(pending)).toBeInTheDocument()
    expect(screen.queryByText(/perché manca un parametro/)).not.toBeInTheDocument()
    const row = await screen.findByRole('row', { name: /IRPEF/ })
    expect(within(row).getAllByRole('cell')[1]).toHaveTextContent('non calcolabile')
    await userEvent.click(row)
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByText('Addizionale comunale (Milano)')).toBeInTheDocument()
    expect(within(panel).getAllByText('non calcolabile').length).toBeGreaterThanOrEqual(2)
  })

  it('names the INPS fact the profile lacks', async () => {
    answers['/api/analytics/fiscal'] = {
      ...PERSON,
      missing_parameters: ['inps_category'],
      person: {
        ...PERSON_ESTIMATE,
        inps: {
          ...PERSON_ESTIMATE.inps,
          amount: null,
          missing: ['Manca la categoria INPS (gestione separata, oppure artigiani e commercianti).'],
        },
      },
      readings: {
        ...PERSON.readings,
        lines: [line('irpef', { estimable: true }), line('inps', { estimable: true })],
      },
    }
    renderPage({ profile: 'solo' })
    expect(await screen.findByText(/Manca la categoria INPS/)).toBeInTheDocument()
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
