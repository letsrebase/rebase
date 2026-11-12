/**
 * «Tasse» (REB-1079): the due items of the year on «Scadenze» with their states, the carried
 * difference an administrator confirms, the advance method and the instalments chosen from a
 * tax's panel, and the plans on «Rate» (a notice's plan entered, changed and deleted). Every
 * figure is a string the server sent; the page adds none.
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

vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: 'admin' } }),
    useCan: (action: Parameters<typeof can>[1]) => can('admin', action),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn(), POST: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const line = (tax: string, overrides: Record<string, unknown> = {}) => ({
  tax,
  estimable: true,
  estimate: '1560.00',
  declared: null,
  paid: null,
  counts: 'estimate',
  ...overrides,
})

const ESTIMATE = {
  year: 2026,
  legal_entity_id: 'le-1',
  estimate: true,
  warning: 'Stima indicativa.',
  revenue: '40000.00',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  law_from_year: 2015,
  frozen: false,
  reading: 'estimate',
  advance_method: 'historical',
  declared: [],
  missing_parameters: [],
  readings: {
    lines: [line('irpef'), line('inps', { estimate: '8133.84' })],
    totals: {
      estimate: '9693.84',
      estimate_left_out: 0,
      declared: null,
      declared_left_out: 2,
      paid: null,
      paid_left_out: 2,
    },
  },
}

const CALENDAR = {
  year: 2026,
  legal_entity_id: 'le-1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  dates: [
    {
      nominal_date: '2026-11-30',
      due_date: '2026-11-30',
      tax: 'income_tax',
      kind: 'second_advance',
      cadence: null,
      covers: 'x',
      source: 'Agenzia delle Entrate',
    },
    {
      nominal_date: '2026-11-16',
      due_date: '2026-11-16',
      tax: 'iva',
      kind: 'periodic_payment',
      cadence: 'monthly',
      covers: 'x',
      source: 'Agenzia delle Entrate',
    },
  ],
}

const item = (overrides: Record<string, unknown>) => ({
  id: 'i-1',
  legal_entity_id: 'le-1',
  direction: 'out',
  state: 'expected',
  counterparty_type: 'tax_office',
  organization_id: null,
  organization_name: null,
  staff_member_id: null,
  tax: 'irpef',
  amount: '700.00',
  currency: 'EUR',
  settled: '0.00',
  remaining: '700.00',
  description: 'Secondo acconto IRPEF 2026. Include +100,00 euro di differenza sull’acconto gia’ pagato: da confermare',
  source_kind: 'tax_advance',
  source_id: 's-1',
  source_rule: null,
  own_date: '2026-11-30',
  due_date: '2026-11-30',
  due_date_estimated: false,
  rule: { kind: 'fixed', depends_on_id: null, offset_days: 0, day_of_month: null, not_after: null },
  dropped_reason: null,
  carry_to_confirm: true,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  ...overrides,
})

const NOTICE = {
  id: 'p-1',
  legal_entity_id: 'le-1',
  label: 'Avviso bonario 2023',
  tax: 'irpef',
  total: '1200.00',
  instalments: [
    { number: 1, due_date: '2026-12-16', principal: '600.00', interest: '10.00', amount: '610.00' },
    { number: 2, due_date: '2027-01-16', principal: '600.00', interest: '20.00', amount: '620.00' },
  ],
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
}

const PLAN_ITEM = item({
  id: 'i-2',
  source_kind: 'notice_plan',
  description: 'Avviso bonario 2023, rata 1 di 2, con 10,00 euro di interessi',
  amount: '610.00',
  due_date: '2026-12-16',
  own_date: '2026-12-16',
  carry_to_confirm: false,
})

let answers: Record<string, unknown>

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
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

function renderPage(tab: TaxesTab) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(queryKeys.modules, spaceModules([], 'company'))
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext.Provider value={ONE}>
        <TaxesPage tab={tab} year={2026} onTabChange={vi.fn()} onYearChange={vi.fn()} />
      </LegalEntityContext.Provider>
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
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 9, 0, 0))
  answers = {
    '/api/analytics/fiscal': ESTIMATE,
    '/api/analytics/fiscal/calendar': CALENDAR,
    '/api/due-items': { items: [item({})], next_cursor: null },
    '/api/f24-groups': { items: [], next_cursor: null },
    '/api/inbox/items': { items: [], next_cursor: null },
    '/api/analytics/fiscal/plans': { legal_entity_id: 'le-1', instalment_plans: [], notice_plans: [] },
  }
  for (const verb of [api.GET, api.PUT, api.POST, api.DELETE]) vi.mocked(verb).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string, options?: { params?: { query?: { state?: string } } }) => {
    if (path === '/api/due-items' && options?.params?.query?.state === 'settled') {
      return ok({ items: [], next_cursor: null })
    }
    return ok(answers[path])
  }) as never)
  vi.mocked(api.PUT).mockImplementation((() => ok({ items: { not_written: [] } })) as never)
  vi.mocked(api.POST).mockImplementation((() => ok({})) as never)
  vi.mocked(api.DELETE).mockImplementation((() => ok(undefined)) as never)
  Reflect.deleteProperty(window, 'matchMedia')
})
afterEach(() => vi.useRealTimers())

describe('«Tasse»: Scadenze with the due items', () => {
  it('draws each item with its amount and state, and the pack’s line only where no item covers it', async () => {
    wide()
    renderPage('dates')
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    // 16/11 holds only the IVA line of the pack; 30/11 holds the advance as an item.
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('IVA di ottobre')
    expect(rows[1]).toHaveTextContent('Secondo acconto IRPEF 2026')
    expect(rows[1]).toHaveTextContent('700,00')
    expect(rows[1]).toHaveTextContent('Da pagare')
    expect(rows[1]).toHaveTextContent('Da confermare')
    // The sentence about the carried difference is not in the row, it is in the panel.
    expect(rows[1]).not.toHaveTextContent('differenza sull')
    expect(rows[1]).not.toHaveTextContent('Secondo acconto 2026 · Imposte sui redditi')
  })

  it('says «Scaduto», and for how long, for an expected item past its date', async () => {
    answers['/api/due-items'] = {
      items: [item({ id: 'i-3', due_date: '2026-11-02', own_date: '2026-11-02', carry_to_confirm: false })],
      next_cursor: null,
    }
    wide()
    renderPage('dates')
    const table = await screen.findByRole('table')
    await waitFor(() => expect(within(table).getAllByRole('row').slice(1)[0]).toHaveTextContent('Scaduto'))
  })

  it('confirms a carried difference from the date’s panel', async () => {
    wide()
    renderPage('dates')
    await userEvent.click(await screen.findByRole('row', { name: /30\/11\/2026/ }))
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).getByText(/Include \+100,00 euro di differenza/)).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Conferma la differenza' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/due-items/{item_id}/carry-confirmation', {
        params: { path: { item_id: 'i-1' } },
      }),
    )
  })

  it('refreshes the list after an item is marked paid, so the button cannot be pressed twice', async () => {
    wide()
    renderPage('dates')
    await userEvent.click(await screen.findByRole('row', { name: /30\/11\/2026/ }))
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    const dueItemReads = () =>
      (vi.mocked(api.GET).mock.calls as unknown as Array<[string]>).filter(([path]) => path === '/api/due-items').length
    const before = dueItemReads()
    await userEvent.click(within(panel).getByRole('button', { name: 'Segna come pagato' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/due-items/{item_id}/settlements', expect.anything()),
    )
    await waitFor(() => expect(dueItemReads()).toBeGreaterThan(before))
  })

  it('offers no confirmation for an item with nothing to confirm', async () => {
    answers['/api/due-items'] = { items: [item({ carry_to_confirm: false })], next_cursor: null }
    wide()
    renderPage('dates')
    await userEvent.click(await screen.findByRole('row', { name: /30\/11\/2026/ }))
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).queryByRole('button', { name: 'Conferma la differenza' })).not.toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Segna come pagato' })).toBeInTheDocument()
  })
})

describe('«Tasse»: Imposte, the method and the instalments', () => {
  it('shows the year’s method and changes it, saying what could not be written', async () => {
    vi.mocked(api.PUT).mockImplementation((() =>
      ok({
        items: {
          not_written: [{ tax: 'irpef', year: 2026, part: 'first_advance', reason: 'forecast method needs the estimate' }],
        },
      })) as never)
    wide()
    renderPage('taxes')
    await userEvent.click(await screen.findByRole('row', { name: /IRPEF/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).getByText('Storico')).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Cambia il metodo dell’acconto' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('combobox', { name: 'Metodo' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Previsionale' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva il metodo' }))
    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/analytics/fiscal/advance-method', {
        params: { query: { legal_entity_id: 'le-1' } },
        body: { year: 2026, method: 'forecast' },
      }),
    )
    // The reason is English text for the report's reader: the dialog says it in its own words.
    expect(await within(dialog).findByText(/Un pagamento non è stato scritto/)).toBeInTheDocument()
    expect(within(dialog).queryByText(/forecast method needs/)).not.toBeInTheDocument()
  })

  it('chooses the instalments of the June payments for a tax', async () => {
    wide()
    renderPage('taxes')
    await userEvent.click(await screen.findByRole('row', { name: /IRPEF/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    await userEvent.click(within(panel).getByRole('button', { name: 'Rateizza giugno' }))
    const dialog = await screen.findByRole('dialog')
    const count = within(dialog).getByLabelText('Numero di rate')
    await userEvent.clear(count)
    await userEvent.type(count, '5')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva la rateazione' }))
    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/analytics/fiscal/instalment-plan', {
        params: { query: { legal_entity_id: 'le-1' } },
        body: { year: 2026, tax: 'irpef', instalments: 5 },
      }),
    )
  })

  it('types the declared figures of a tax, an empty one staying not given', async () => {
    wide()
    renderPage('taxes')
    await userEvent.click(await screen.findByRole('row', { name: /IRPEF/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    await userEvent.click(within(panel).getByRole('button', { name: 'Digita il dichiarato' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva il dichiarato' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Indica almeno una cifra')
    expect(api.PUT).not.toHaveBeenCalled()
    await userEvent.type(within(dialog).getByLabelText('Imposta dovuta'), '1.234,50')
    await userEvent.type(within(dialog).getByLabelText('Saldo'), '-80')
    await userEvent.type(within(dialog).getByLabelText('Acconti'), '-1')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva il dichiarato' }))
    expect(await within(dialog).findByText('Questa cifra non può essere negativa.')).toBeInTheDocument()
    await userEvent.clear(within(dialog).getByLabelText('Acconti'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva il dichiarato' }))
    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/analytics/fiscal/declared', {
        params: { query: { legal_entity_id: 'le-1' } },
        body: { tax: 'irpef', year: 2026, tax_due: '1234.50', balance: '-80', advances: null },
      }),
    )
  })

  it('offers neither for INPS, which has no advances to split', async () => {
    wide()
    renderPage('taxes')
    await userEvent.click(await screen.findByRole('row', { name: /Contributi INPS/ }))
    const panel = await screen.findByRole('complementary', { name: 'Imposta aperta' })
    expect(within(panel).queryByRole('button', { name: 'Rateizza giugno' })).not.toBeInTheDocument()
  })

  it('links the fiscal settings when a parameter is missing', async () => {
    answers['/api/analytics/fiscal'] = {
      ...ESTIMATE,
      missing_parameters: ['profitability_coefficient'],
      readings: { ...ESTIMATE.readings, lines: [line('substitute_tax', { estimate: null })] },
    }
    wide()
    renderPage('taxes')
    const link = await screen.findByRole('link', { name: 'Apri i parametri fiscali' })
    expect(link).toHaveAttribute('href', '/app/settings/legal-entities')
    expect(screen.getByText(/coefficiente di redditività/)).toBeInTheDocument()
  })
})

const CREDIT = item({
  id: 'i-9',
  tax: 'iva',
  source_kind: 'tax_credit',
  amount: '-200.00',
  remaining: '-200.00',
  description: 'Credito IVA 2025',
  carry_to_confirm: false,
})

const GROUP = (overrides: Record<string, unknown> = {}) => ({
  id: 'g-1',
  legal_entity_id: 'le-1',
  payment_date: '2026-11-30',
  state: 'open',
  note: null,
  total: '500.00',
  debit_total: '700.00',
  credit_total: '200.00',
  lines: [
    { due_item_id: 'i-1', source_kind: 'tax_advance', tax: 'irpef', description: 'x', amount: '700.00', is_credit: false, credit_origin: null, line: 'Secondo acconto IRPEF', code: '4034', code_kind: 'tax_code', code_label: '4034', code_to_verify: false, reference_year: 2026, reference_period: null, released: false, used_amount: null },
    { due_item_id: 'i-9', source_kind: 'tax_credit', tax: 'iva', description: 'x', amount: '-200.00', is_credit: true, credit_origin: null, line: null, code: null, code_kind: null, code_label: 'codice da verificare', code_to_verify: true, reference_year: null, reference_period: null, released: false, used_amount: null },
  ],
  receipts: [],
  bank_movement_id: null,
  settled_on: null,
  settled_by: null,
  credit_left: null,
  created_by_type: 'user',
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  ...overrides,
})

describe('«Tasse»: F24 and credits', () => {
  beforeEach(() => {
    answers['/api/due-items'] = { items: [item({ carry_to_confirm: false }), CREDIT], next_cursor: null }
  })

  it('says «F24 da creare» and groups the items of the date, the credit among them', async () => {
    wide()
    renderPage('dates')
    const row = await screen.findByRole('row', { name: /30\/11\/2026/ })
    expect(row).toHaveTextContent('F24 da creare')
    expect(row).toHaveTextContent('-200,00')
    await userEvent.click(row)
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    await userEvent.click(within(panel).getByRole('button', { name: 'Raggruppa in un F24' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/f24-groups', { body: { due_item_ids: ['i-1', 'i-9'] } }),
    )
  })

  it('shows an open group with the codes, the server’s net, and ungroups it', async () => {
    answers['/api/f24-groups'] = { items: [GROUP()], next_cursor: null }
    wide()
    renderPage('dates')
    const row = await screen.findByRole('row', { name: /30\/11\/2026/ })
    expect(row).toHaveTextContent('F24 creato')
    expect(row).toHaveTextContent('Netto 500,00')
    await userEvent.click(row)
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).getByText('Codice 4034 · anno 2026')).toBeInTheDocument()
    expect(within(panel).getByText(/codice da verificare/)).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Segna come pagato' })).not.toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Sciogli l’F24' }))
    await userEvent.click(within(panel).getByRole('button', { name: 'Sciogli il gruppo' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/f24-groups/{group_id}/ungroup', {
        params: { path: { group_id: 'g-1' } },
      }),
    )
  })

  it('links the receipt of a paid group', async () => {
    answers['/api/f24-groups'] = {
      items: [
        GROUP({
          state: 'settled',
          receipts: [{ id: 'r-1', inbox_item_id: 'in-1', original_name: 'ricevuta-f24.pdf', attached_at: '2026-11-30T10:00:00Z' }],
        }),
      ],
      next_cursor: null,
    }
    wide()
    renderPage('dates')
    const row = await screen.findByRole('row', { name: /30\/11\/2026/ })
    expect(row).toHaveTextContent('F24 pagato · ricevuta')
    await userEvent.click(row)
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).getByRole('link', { name: 'ricevuta-f24.pdf' })).toHaveAttribute(
      'href',
      '/api/inbox/items/in-1/file',
    )
    expect(within(panel).queryByRole('button', { name: 'Sciogli l’F24' })).not.toBeInTheDocument()
  })
})

describe('«Tasse»: an F24 with nothing to pay', () => {
  const NOTHING = (overrides: Record<string, unknown> = {}) =>
    GROUP({ total: '-130.00', debit_total: '100.00', credit_total: '230.00', ...overrides })

  beforeEach(() => {
    answers['/api/due-items'] = { items: [item({ carry_to_confirm: false }), CREDIT], next_cursor: null }
  })

  it('offers «Segna come presentato» instead of a bank match, and files it on the day given', async () => {
    answers['/api/f24-groups'] = { items: [NOTHING()], next_cursor: null }
    wide()
    renderPage('dates')
    const row = await screen.findByRole('row', { name: /30\/11\/2026/ })
    await userEvent.click(row)
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).getByText(/nessun movimento in banca lo salda/)).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Segna come presentato' }))
    expect(within(panel).getByLabelText('Presentato il')).toHaveValue('2026-11-10')
    await userEvent.click(within(panel).getByRole('button', { name: 'Conferma' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/f24-groups/{group_id}/file', {
        params: { path: { group_id: 'g-1' } },
        body: { filed_on: '2026-11-10', receipt_inbox_item_id: null },
      }),
    )
  })

  it('does not offer it for a group with an amount to pay', async () => {
    answers['/api/f24-groups'] = { items: [GROUP()], next_cursor: null }
    wide()
    renderPage('dates')
    await userEvent.click(await screen.findByRole('row', { name: /30\/11\/2026/ }))
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).queryByRole('button', { name: 'Segna come presentato' })).not.toBeInTheDocument()
  })

  it('shows a filed group with what is left of its credits and reopens it', async () => {
    answers['/api/f24-groups'] = {
      items: [NOTHING({ state: 'settled', settled_by: 'filing', settled_on: '2026-11-10', credit_left: '130.00' })],
      next_cursor: null,
    }
    wide()
    renderPage('dates')
    const row = await screen.findByRole('row', { name: /30\/11\/2026/ })
    expect(row).toHaveTextContent('F24 presentato')
    await userEvent.click(row)
    const panel = await screen.findByRole('complementary', { name: 'Scadenza aperta' })
    expect(within(panel).getByText(/Presentato il 10\/11\/2026/)).toBeInTheDocument()
    expect(within(panel).getByText(/Resta un credito di/)).toHaveTextContent('130,00')
    expect(within(panel).queryByRole('button', { name: 'Segna come presentato' })).not.toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Riapri l’F24' }))
    await userEvent.click(within(panel).getByRole('button', { name: 'Riapri il gruppo' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/f24-groups/{group_id}/reopen', {
        params: { path: { group_id: 'g-1' } },
      }),
    )
  })
})

describe('«Tasse»: a solo flat-rate space', () => {
  function renderSolo() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(queryKeys.modules, spaceModules([], 'solo'))
    return render(
      <QueryClientProvider client={client}>
        <LegalEntityContext.Provider value={ONE}>
          <TaxesPage tab="taxes" year={2026} onTabChange={vi.fn()} onYearChange={vi.fn()} />
        </LegalEntityContext.Provider>
      </QueryClientProvider>,
    )
  }

  it('draws «Rate» only when a plan exists, with no strong action', async () => {
    answers['/api/analytics/fiscal/plans'] = { legal_entity_id: 'le-1', instalment_plans: [], notice_plans: [NOTICE] }
    answers['/api/due-items'] = { items: [PLAN_ITEM], next_cursor: null }
    renderSolo()
    const section = await screen.findByRole('region', { name: 'Rate' })
    expect(within(section).getByText('Avviso bonario 2023')).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Nuovo piano' })).not.toBeInTheDocument()
  })

  it('draws no «Rate» section while there is no plan', async () => {
    renderSolo()
    await screen.findByText('Stima indicativa.')
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/analytics/fiscal/plans', expect.anything()))
    expect(screen.queryByRole('region', { name: 'Rate' })).not.toBeInTheDocument()
  })
})

describe('«Tasse»: Rate', () => {
  beforeEach(() => {
    answers['/api/analytics/fiscal/plans'] = {
      legal_entity_id: 'le-1',
      instalment_plans: [{ legal_entity_id: 'le-1', tax: 'irpef', tax_year: 2025, instalments: 4, updated_at: '2026-11-01T00:00:00Z' }],
      notice_plans: [NOTICE],
    }
    answers['/api/due-items'] = { items: [PLAN_ITEM], next_cursor: null }
  })

  it('lists a row per plan with the notice’s amount, its instalments and the next one', async () => {
    wide()
    renderPage('plans')
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Rateazione IRPEF 2025')
    expect(rows[1]).toHaveTextContent('Avviso bonario 2023')
    expect(rows[1]).toHaveTextContent('1.200,00')
    expect(rows[1]).toHaveTextContent('16/12/2026')
    expect(rows[1]).toHaveTextContent('Da pagare')
  })

  it('says what lives here when there is no plan', async () => {
    answers['/api/analytics/fiscal/plans'] = { legal_entity_id: 'le-1', instalment_plans: [], notice_plans: [] }
    wide()
    renderPage('plans')
    expect(await screen.findByText(/Qui compaiono i piani di rate/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nuovo piano' })).toBeInTheDocument()
  })

  it('enters a notice’s plan, the amounts as strings and the empty interest as zero', async () => {
    answers['/api/analytics/fiscal/plans'] = { legal_entity_id: 'le-1', instalment_plans: [], notice_plans: [] }
    wide()
    renderPage('plans')
    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo piano' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Avviso'), 'Cartella 2024')
    await userEvent.type(within(dialog).getByLabelText(/Importo dell’avviso/), '1.200,50')
    await userEvent.type(within(dialog).getByLabelText('Scadenza della rata 1'), '2026-12-16')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Registra il piano' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/analytics/fiscal/notice-plans', {
        params: { query: { legal_entity_id: 'le-1' } },
        body: {
          label: 'Cartella 2024',
          tax: 'irpef',
          total: '1200.50',
          instalments: [{ due_date: '2026-12-16', interest: '0.00' }],
        },
      }),
    )
  })

  it('refuses an amount it cannot read before asking the server', async () => {
    wide()
    renderPage('plans')
    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo piano' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Avviso'), 'Cartella')
    await userEvent.type(within(dialog).getByLabelText(/Importo dell’avviso/), '12.4567')
    await userEvent.type(within(dialog).getByLabelText('Scadenza della rata 1'), '2026-12-16')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Registra il piano' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('non è chiaro')
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('changes and deletes a notice’s plan from its panel', async () => {
    wide()
    renderPage('plans')
    await userEvent.click(await screen.findByRole('row', { name: /Avviso bonario 2023/ }))
    const panel = await screen.findByRole('complementary', { name: 'Piano aperto' })
    expect(within(panel).getByText(/Quota 600,00 €, interessi 10,00 €/)).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Modifica' }))
    const dialog = await screen.findByRole('dialog')
    const label = within(dialog).getByLabelText('Avviso')
    await userEvent.clear(label)
    await userEvent.type(label, 'Avviso bonario 2023 bis')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva le modifiche' }))
    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/analytics/fiscal/notice-plans/{plan_id}', {
        params: { path: { plan_id: 'p-1' } },
        body: expect.objectContaining({ label: 'Avviso bonario 2023 bis', total: '1200.00' }),
      }),
    )
    await userEvent.click(within(panel).getByRole('button', { name: 'Elimina' }))
    await userEvent.click(within(panel).getByRole('button', { name: 'Elimina il piano' }))
    await waitFor(() =>
      expect(api.DELETE).toHaveBeenCalledWith('/api/analytics/fiscal/notice-plans/{plan_id}', {
        params: { path: { plan_id: 'p-1' } },
      }),
    )
  })

  it('removes the instalments of the June payments', async () => {
    wide()
    renderPage('plans')
    await userEvent.click(await screen.findByRole('row', { name: /Rateazione IRPEF 2025/ }))
    const panel = await screen.findByRole('complementary', { name: 'Piano aperto' })
    await userEvent.click(within(panel).getByRole('button', { name: 'Togli la rateazione' }))
    await waitFor(() =>
      expect(api.PUT).toHaveBeenCalledWith('/api/analytics/fiscal/instalment-plan', {
        params: { query: { legal_entity_id: 'le-1' } },
        body: { year: 2025, tax: 'irpef', instalments: 1 },
      }),
    )
  })
})
