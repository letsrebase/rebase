/**
 * The cash schedule's states (the brief `2026-11-10-cash-and-schedule-brief.md`, the table of
 * «Cassa» and the tab «Scadenziario»): what a fixture says is what is drawn, every figure as the
 * string the server sent, and what the viewer may not read is absent.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import type { ModuleKey } from '@/lib/modules'
import { spaceModules } from '@/lib/modules.testing'
import { CashSchedule } from './CashSchedule'
import type { CashSchedule as Schedule, DueItem } from './queries'

const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) => can(session.role, action),
    useIsAdmin: () => session.role === 'admin',
  }
})

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(detail: string, status = 500) {
  return {
    error: { type: 'about:blank', title: 'Errore', status, detail },
    response: new Response(null, { status }),
  } as never
}

function viewport(wide: boolean, inline: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: query.includes('1024px') ? inline : wide,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia
}

const week = (start: string, end: string, extra: Partial<Schedule['weeks'][number]> = {}) => ({
  start,
  end,
  is_current: false,
  in_expected: '0.00',
  in_forecast: '0.00',
  out_expected: '0.00',
  out_forecast: '0.00',
  net: '0.00',
  balance_end: '10000.00',
  below_zero: false,
  item_count: 0,
  has_forecast: false,
  has_estimated_date: false,
  ...extra,
})

const account = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  legal_entity_id: 'le-1',
  legal_entity_name: 'Alfa Servizi srl',
  name: `Conto ${id}`,
  bank: 'Banco BPM',
  currency: 'EUR',
  balance: '12000.00',
  balance_as_of: '2026-11-06',
  stale_days: 4,
  is_stale: false,
  ...extra,
})

function company(extra: Partial<Schedule> = {}): Schedule {
  return {
    calculated_at: '2026-11-10T11:42:00Z',
    today: '2026-11-10',
    legal_entity_id: null,
    horizon_weeks: 13,
    horizon_options: [4, 13, 26],
    profile: 'company',
    cash_enabled: true,
    currency: 'EUR',
    show_outgoing: true,
    show_balance: true,
    accounts: [
      account('a1'),
      account('a2', { name: 'Conto vecchio', balance_as_of: '2026-09-30', stale_days: 41, is_stale: true }),
      account('a3', { name: 'Conto USD', currency: 'USD', balance: '1350.00' }),
    ] as never,
    total_balance: {
      amount: '24000.00',
      currency: 'EUR',
      as_of: '2026-11-06',
      excluded_currencies: ['USD'],
      accounts_without_balance: 0,
      below_zero: false,
    } as never,
    late_receivable: { count: 3, total: '3660.00', oldest_days: 41 },
    late_payable: { count: 1, total: '1200.00', oldest_days: 5 },
    weeks: [
      week('2026-11-09', '2026-11-15', {
        is_current: true,
        in_expected: '5000.00',
        in_forecast: '700.00',
        out_expected: '1000.00',
        out_forecast: '0.00',
        net: '4700.00',
        balance_end: '28700.00',
        item_count: 3,
        has_forecast: true,
        has_estimated_date: true,
      }),
      week('2026-11-16', '2026-11-22', { balance_end: '-250.00', below_zero: true, net: '-29000.00', item_count: 1 }),
    ],
    taxes: null,
    alerts: [
      { kind: 'below_zero', week_start: '2026-11-16', week_end: '2026-11-22' },
      { kind: 'missing_statement', account_name: 'Conto vecchio', period_from: '2026-10-01', period_to: '2026-10-31' },
    ],
    has_open_items: true,
    ...extra,
  } as Schedule
}

const band = (code: string, label: string, amount: string) => ({
  code,
  label,
  from: null,
  to: null,
  amount,
  number: 1,
  share: 0.5,
  link: code === 'expired' ? '/app/invoices?overdue=true' : null,
})

const BANDS = [
  band('expired', 'Scaduto', '3660.00'),
  band('entro_30', 'Entro 30 giorni', '200.00'),
  band('da_31_a_60', 'Da 31 a 60 giorni', '300.00'),
  band('da_61_a_90', 'Da 61 a 90 giorni', '400.00'),
  band('oltre_90', 'Oltre 90 giorni', '500.00'),
  band('without_due_date', 'Senza scadenza', '600.00'),
]

const RECEIVABLES = {
  calculated_at: '2026-11-10T11:42:00Z',
  today: '2026-11-10',
  total: '8000.00',
  bands: BANDS,
  by_month: [],
  by_customer: [],
  overdue: [
    {
      invoice_id: 'f-1',
      number: '2026/1',
      customer_id: 'c-1',
      customer: 'Acme S.r.l.',
      due_date: '2026-09-30',
      days_of_delay: 41,
      amount: '3660.00',
      sent_payment_reminders: 1,
      last_payment_reminder_at: '2026-10-15',
    },
  ],
  total_overdue: 1,
}

const PAYABLES = {
  calculated_at: '2026-11-10T11:42:00Z',
  today: '2026-11-10',
  legal_entity_id: null,
  total: '4000.00',
  bands: BANDS,
  overdue: [
    {
      due_item_id: 'd-9',
      counterparty: 'Fornitore Uno',
      description: 'Fattura 77',
      due_date: '2026-11-05',
      days_of_delay: 5,
      amount: '1200.00',
    },
  ],
  total_overdue: 1,
}

const item = (id: string, extra: Partial<DueItem> = {}): DueItem =>
  ({
    id,
    legal_entity_id: 'le-1',
    direction: 'in',
    state: 'expected',
    counterparty_type: 'organization',
    organization_id: 'o-1',
    organization_name: 'Beta S.r.l.',
    staff_member_id: null,
    tax: null,
    amount: '1464.00',
    currency: 'EUR',
    settled: '0.00',
    remaining: '1464.00',
    description: 'Fattura 2026/44',
    source_kind: 'sales_invoice',
    source_id: 'inv-44',
    source_rule: null,
    own_date: '2026-11-12',
    due_date: '2026-11-12',
    due_date_estimated: false,
    rule: { kind: 'fixed', depends_on_id: null, offset_days: 0, day_of_month: null, not_after: null },
    dropped_reason: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...extra,
  }) as DueItem

type Answers = {
  schedule?: unknown
  items?: DueItem[]
  modules?: readonly ModuleKey[]
}

function answer({ schedule = company(), items = [], modules = [] }: Answers = {}) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    switch (path) {
      case '/api/cash/schedule':
        return Promise.resolve(ok(schedule))
      case '/api/dashboard/receivables':
        return Promise.resolve(ok(RECEIVABLES))
      case '/api/cash/payables':
        return Promise.resolve(ok(PAYABLES))
      case '/api/due-items':
        return Promise.resolve(ok({ items, next_cursor: null }))
      case '/api/settings/modules':
        return Promise.resolve(ok(spaceModules(modules)))
      default:
        return Promise.resolve(ok({}))
    }
  }) as never)
}

const ONE_ENTITY: LegalEntityValue = {
  legal_entities: [],
  selected: null,
  select: vi.fn(),
  several: false,
  scoped: false,
  pinned: false,
  loaded: true,
  byId: () => undefined,
}

function renderSchedule(entities: LegalEntityValue = ONE_ENTITY) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext value={entities}>
        <CashSchedule />
      </LegalEntityContext>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  session.role = 'admin'
  viewport(true, true)
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
})

afterEach(() => {
  Reflect.deleteProperty(window, 'matchMedia')
})

describe('CashSchedule, a company administrator', () => {
  it('draws the band, the balances, the late figures and the weeks from the server’s strings', async () => {
    answer()
    renderSchedule()

    // The band: the worst first, the rest counted, nothing to close it with.
    expect(await screen.findByText(/scende sotto zero nella settimana dal 16\/11 al 22\/11\/2026/)).toBeInTheDocument()
    expect(screen.getByText('e 1 altra')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /chiudi/i })).not.toBeInTheDocument()

    // The balances: the total, and one card per account with its own currency and age.
    expect(screen.getByRole('group', { name: 'Saldo oggi' })).toHaveTextContent(/24\.000,00/)
    expect(screen.getByText(/Non include i conti in USD/)).toBeInTheDocument()
    expect(screen.getByText(/1\.350,00\s*USD/)).toBeInTheDocument()
    expect(screen.getByText('da 41 giorni')).toBeInTheDocument()

    // The late figures, apart from the weeks, and the note under the table.
    const lateIn = screen.getByRole('group', { name: 'In ritardo da incassare' })
    expect(lateIn).toHaveTextContent(/3\.660,00/)
    expect(lateIn).toHaveTextContent('3 voci, la più vecchia da 41 giorni')
    expect(screen.getByRole('group', { name: 'In ritardo da pagare' })).toHaveTextContent(/1\.200,00/)
    expect(screen.getByText(/Il saldo non conta ciò che è già scaduto/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Vai alle voci in ritardo' })).toBeInTheDocument()

    // The weeks: one balance, no «Saldo certo», the current week marked, forecast named.
    const table = screen.getByRole('table', { name: 'Le settimane' })
    const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
    expect(headers).toEqual(['Settimana', 'Entrate', 'Uscite', 'Netto', 'Saldo a fine settimana'])
    expect(screen.queryByText(/saldo certo/i)).not.toBeInTheDocument()
    expect(within(table).getByRole('button', { name: /09\/11 al 15\/11/ })).toHaveTextContent(
      'Questa settimana · 3 voci, con date circa',
    )
    expect(within(table).getByText(/700,00.*previsti/)).toBeInTheDocument()
    expect(within(table).getByText(/-250,00/)).toBeInTheDocument()
    expect(within(table).getByText('sotto zero')).toBeInTheDocument()

    // The schedule's own sections, as the brief says; the payables one has no «Solleciti».
    expect(await screen.findByRole('group', { name: 'Da incassare' })).toHaveTextContent(/8\.000,00/)
    expect(await screen.findByRole('group', { name: 'Da pagare' })).toHaveTextContent(/4\.000,00/)
    expect(screen.getAllByText('Scaduto da 41 giorni')).toHaveLength(1)
    expect(screen.getByText('Scaduto da 5 giorni')).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: 'Solleciti' })).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Apri Economica' })).toHaveAttribute('href', '/app')
  })

  it('links an account card to its page and never draws a zero for an unknown balance', async () => {
    answer({ schedule: company({ accounts: [account('a1', { balance: null, balance_as_of: null, stale_days: null })] as never }) })
    renderSchedule()
    const link = await screen.findByRole('link', { name: /Conto a1/ })
    expect(link).toHaveAttribute('href', '/app/cash/accounts/$accountId')
    expect(link).toHaveTextContent('Saldo non ancora noto')
    expect(link).not.toHaveTextContent('0,00')
  })

  it('says how many accounts are not in the total', async () => {
    answer({
      schedule: company({
        total_balance: { amount: '24000.00', currency: 'EUR', as_of: null, excluded_currencies: [], accounts_without_balance: 2 } as never,
      }),
    })
    renderSchedule()
    expect(await screen.findByText('2 conti senza saldo iniziale non sono nel totale.')).toBeInTheDocument()
  })

  it('draws a balance below zero in danger with the words, from the server’s flags only', async () => {
    answer({
      schedule: company({
        accounts: [account('a1', { balance: '-80.00', below_zero: true }), account('a2', { balance: '500.00' })] as never,
        total_balance: {
          amount: '-80.00',
          currency: 'EUR',
          as_of: null,
          excluded_currencies: [],
          accounts_without_balance: 0,
          below_zero: true,
        } as never,
      }),
    })
    renderSchedule()
    const total = await screen.findByRole('group', { name: 'Saldo oggi' })
    expect(within(total).getByText('sotto zero')).toBeInTheDocument()
    expect(within(total).getByText(/-80,00/).className).toContain('text-destructive')
    const accounts = screen.getAllByRole('link', { name: /Conto a/ })
    expect(within(accounts[0]!).getByText('sotto zero')).toBeInTheDocument()
    expect(within(accounts[1]!).queryByText('sotto zero')).not.toBeInTheDocument()
  })

  it('says «da 1 giorno» in the singular, on the card and on the late figure', async () => {
    answer({
      schedule: company({
        accounts: [account('a1', { is_stale: true, stale_days: 1 })] as never,
        late_receivable: { count: 1, total: '10.00', oldest_days: 1 },
      }),
    })
    renderSchedule()
    expect(await screen.findByText('da 1 giorno')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'In ritardo da incassare' })).toHaveTextContent('1 voce, la più vecchia da 1 giorno')
  })

  it('never shows one azienda’s schedule under the name of another after the sidebar switches', async () => {
    answer()
    const original = vi.mocked(api.GET).getMockImplementation()!
    // The executor form: this app's tsconfig `lib` predates `Promise.withResolvers`.
    let release: (value: unknown) => void = () => {}
    const gate = new Promise((resolve) => {
      release = resolve
    })
    vi.mocked(api.GET).mockImplementation(((path: string, options?: { params?: { query?: { legal_entity_id?: string } } }) => {
      if (path === '/api/cash/schedule' && options?.params?.query?.legal_entity_id === 'le-2') {
        return gate
      }
      return (original as (path: string) => unknown)(path)
    }) as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const tree = (value: LegalEntityValue) => (
      <QueryClientProvider client={client}>
        <LegalEntityContext value={value}>
          <CashSchedule />
        </LegalEntityContext>
      </QueryClientProvider>
    )
    const view = render(tree(ONE_ENTITY))
    expect(await screen.findByRole('group', { name: 'Saldo oggi' })).toHaveTextContent(/24\.000,00/)

    view.rerender(tree({ ...ONE_ENTITY, several: true, selected: 'le-2' }))
    expect(await screen.findByRole('status', { name: 'Leggo le scadenze…' })).toBeInTheDocument()
    expect(screen.queryByText(/24\.000,00/)).not.toBeInTheDocument()

    release(
      ok(
        company({
          legal_entity_id: 'le-2',
          total_balance: { amount: '5000.00', currency: 'EUR', as_of: null, excluded_currencies: [], accounts_without_balance: 0, below_zero: false } as never,
        }),
      ),
    )
    expect(await screen.findByRole('group', { name: 'Saldo oggi' })).toHaveTextContent(/5\.000,00/)
  })

  it('shows four accounts and then «Tutti i conti (N)»', async () => {
    const accounts = ['a1', 'a2', 'a3', 'a4', 'a5'].map((id) => account(id))
    answer({ schedule: company({ accounts: accounts as never }) })
    renderSchedule()
    await screen.findByRole('group', { name: 'Saldo oggi' })
    expect(screen.queryByRole('link', { name: /Conto a5/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tutti i conti (5)' }))
    expect(screen.getByRole('link', { name: /Conto a5/ })).toBeInTheDocument()
  })

  it('names the entity on the account cards and on the items only under «Tutte le aziende»', async () => {
    answer({ items: [item('i-1')] })
    const several: LegalEntityValue = {
      ...ONE_ENTITY,
      several: true,
      byId: () => ({ id: 'le-1', name: 'Alfa Servizi srl' }) as never,
    }
    renderSchedule(several)
    expect(await screen.findByRole('group', { name: 'Saldo oggi, tutte le aziende' })).toBeInTheDocument()
    expect(screen.getAllByText('Alfa Servizi srl').length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole('button', { name: /09\/11 al 15\/11/ }))
    const panel = await screen.findByRole('complementary', { name: 'Voci della settimana' })
    expect(await within(panel).findByText('Alfa Servizi srl')).toBeInTheDocument()
  })

  it('sends the horizon the server offers and the entity the sidebar chose', async () => {
    answer()
    renderSchedule({ ...ONE_ENTITY, several: true, selected: 'le-2' })
    await screen.findByRole('combobox', { name: 'Orizzonte' })
    expect(screen.getByRole('combobox', { name: 'Orizzonte' })).toHaveTextContent('13 settimane')
    expect(api.GET).toHaveBeenCalledWith('/api/cash/schedule', { params: { query: { legal_entity_id: 'le-2' } } })
    expect(screen.getByRole('group', { name: 'Saldo oggi' })).toBeInTheDocument()
  })
})

describe('CashSchedule, a week’s items', () => {
  it('opens the items of the selected week from the server and settles one by hand', async () => {
    answer({
      items: [
        item('i-1'),
        item('i-2', {
          state: 'forecast',
          source_kind: 'recurring_expense',
          direction: 'out',
          organization_name: 'Hosting srl',
          description: 'Canone',
          due_date_estimated: true,
        }),
        item('i-3', { settled: '1000.00', remaining: '464.00', organization_name: 'Gamma', description: 'Fattura 2026/50' }),
      ],
    })
    vi.mocked(api.POST).mockResolvedValue(ok({}))
    renderSchedule()
    await userEvent.click(await screen.findByRole('button', { name: /09\/11 al 15\/11/ }))

    // The current week starts today: the days before it are the late figure's.
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/due-items', {
        params: { query: { due_from: '2026-11-10', due_to: '2026-11-15', limit: 50 } },
      }),
    )
    const panel = await screen.findByRole('complementary', { name: 'Voci della settimana' })
    expect(await within(panel).findByText('Beta S.r.l.')).toBeInTheDocument()
    expect(within(panel).getByText('Fattura 2026/44')).toBeInTheDocument()
    expect(within(panel).getByText('Da incassare')).toBeInTheDocument()
    // Forecast: its own word, a date that follows another item, the sentence of the rule.
    expect(within(panel).getByText('Previsto')).toBeInTheDocument()
    expect(within(panel).getByText(/circa 12\/11\/2026, in attesa del pagamento/)).toBeInTheDocument()
    // Partly paid: the server’s two figures.
    expect(within(panel).getByText(/Incassati 1\.000,00.*restano 464,00/)).toBeInTheDocument()

    await userEvent.click(within(panel).getAllByRole('button', { name: /Beta S\.r\.l\./ })[0]!)
    expect(within(panel).getByRole('link', { name: 'Apri la fattura' })).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: 'Segna come incassato' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/due-items/{item_id}/settlements', {
        params: { path: { item_id: 'i-1' } },
        body: { amount: '1464.00', settled_on: '2026-11-10' },
      }),
    )
  })

  it('gives a forecast no act, and a collaborator no way to pay what goes out', async () => {
    session.role = 'collaborator'
    answer({
      items: [
        item('i-1', { direction: 'out', organization_name: 'Fornitore Due', source_kind: 'purchase', source_id: 'p-1' }),
        item('i-2', { state: 'forecast', organization_name: 'Previsione' }),
      ],
    })
    renderSchedule()
    await userEvent.click(await screen.findByRole('button', { name: /09\/11 al 15\/11/ }))
    const panel = await screen.findByRole('complementary', { name: 'Voci della settimana' })
    await userEvent.click(await within(panel).findByRole('button', { name: /Fornitore Due/ }))
    expect(within(panel).queryByRole('button', { name: 'Segna come pagato' })).not.toBeInTheDocument()
    expect(within(panel).getByRole('link', { name: 'Apri la fattura' })).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: /Previsione/ }))
    expect(within(panel).queryByRole('button', { name: /Segna come/ })).not.toBeInTheDocument()
  })

  it('draws a row of a kind it does not know, with its description, instead of dropping it', async () => {
    answer({
      items: [
        item('i-1'),
        item('i-2', { source_kind: 'brand_new_kind', source_id: null, organization_name: null, counterparty_type: 'tax_office', description: 'Voce nuova' }),
      ],
    })
    renderSchedule()
    await userEvent.click(await screen.findByRole('button', { name: /09\/11 al 15\/11/ }))
    const panel = await screen.findByRole('complementary', { name: 'Voci della settimana' })
    expect(await within(panel).findByText('Voce nuova')).toBeInTheDocument()
    // The week row and the panel agree: the server's count as it is, every row it sent drawn.
    expect(within(panel).getByText('3 voci')).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: /Voce nuova/ }))
    expect(within(panel).getByText('Altra voce')).toBeInTheDocument()
  })

  it('refreshes the invoice’s own set and every dashboard read after a collection', async () => {
    answer({ items: [item('i-1')] })
    vi.mocked(api.POST).mockResolvedValue(ok({}))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidated = vi.spyOn(client, 'invalidateQueries')
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={ONE_ENTITY}>
          <CashSchedule />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    await userEvent.click(await screen.findByRole('button', { name: /09\/11 al 15\/11/ }))
    const panel = await screen.findByRole('complementary', { name: 'Voci della settimana' })
    await userEvent.click(await within(panel).findByRole('button', { name: /Beta S\.r\.l\./ }))
    await userEvent.click(within(panel).getByRole('button', { name: 'Segna come incassato' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const keys = () => invalidated.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey))
    await waitFor(() => expect(keys()).toContain(JSON.stringify(['dashboard'])))
    expect(keys()).toEqual(
      expect.arrayContaining([
        JSON.stringify(['invoices-list']),
        JSON.stringify(['ceilings']),
        JSON.stringify(['invoice', 'inv-44']),
      ]),
    )
  })

  it('opens a week full screen as a card on the phone', async () => {
    viewport(false, false)
    answer({ items: [item('i-1', { direction: 'out', organization_name: 'Fornitore Uno' })] })
    renderSchedule()
    expect(await screen.findByRole('list', { name: 'Le settimane' })).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Le settimane' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /09\/11 al 15\/11/ }))
    const sheet = await screen.findByRole('dialog')
    await userEvent.click(await within(sheet).findByRole('button', { name: /Fornitore Uno/ }))
    // A visible button, not a hover control, on an open item of money going out.
    expect(within(sheet).getByRole('button', { name: 'Segna come pagato' })).toBeVisible()
  })
})

describe('CashSchedule, the states of the brief', () => {
  it('waits with one sentence, and shows the error alone when the read fails', async () => {
    vi.mocked(api.GET).mockImplementation((() => new Promise(() => {})) as never)
    const { unmount } = renderSchedule()
    expect(screen.getByRole('status', { name: 'Leggo le scadenze…' })).toBeInTheDocument()
    unmount()

    vi.mocked(api.GET).mockReset()
    vi.mocked(api.GET).mockResolvedValue(failed('boom'))
    renderSchedule()
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
    expect(screen.queryByText(/Saldo oggi/)).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('says there is no account and offers the administrator one button', async () => {
    answer({ schedule: company({ accounts: [], total_balance: null, show_balance: false }) })
    renderSchedule()
    expect(await screen.findByText(/Nessun conto collegato\. Aggiungi un conto/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Aggiungi un conto' })).toBeInTheDocument()
    // The invoices' schedule is still there, without a balance column.
    const headers = within(screen.getByRole('table', { name: 'Le settimane' }))
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent)
    expect(headers).not.toContain('Saldo a fine settimana')
  })

  it('promises a solo space today\'s balance only, never the weeks to come', async () => {
    answer({
      schedule: company({
        profile: 'solo',
        show_outgoing: false,
        accounts: [],
        total_balance: null,
        show_balance: false,
      }),
    })
    renderSchedule()
    const sentence = await screen.findByText(/Nessun conto collegato\. Aggiungi un conto/)
    expect(sentence).toHaveTextContent('il saldo di oggi.')
    expect(sentence).not.toHaveTextContent('prossime settimane')
  })

  it('draws no balance, no account and no band for a role that may not read them', async () => {
    session.role = 'collaborator'
    answer({
      schedule: company({ accounts: null, total_balance: null, show_balance: false, alerts: [] }),
    })
    renderSchedule()
    await screen.findByRole('table', { name: 'Le settimane' })
    expect(screen.queryByRole('group', { name: /Saldo oggi/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/Nessun conto collegato/)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Conto/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('status', { name: '' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Il saldo non conta/)).not.toBeInTheDocument()
    expect(await screen.findByRole('group', { name: 'Da pagare' })).toBeInTheDocument()
  })

  it('with the module cash off keeps both directions and the late lists, and no balance', async () => {
    answer({
      schedule: company({ cash_enabled: false, accounts: null, total_balance: null, show_balance: false, alerts: [] }),
    })
    renderSchedule()
    expect(await screen.findByRole('group', { name: 'Da incassare' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Da pagare' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: /Saldo oggi/ })).not.toBeInTheDocument()
    expect(screen.getByText('Scaduto da 5 giorni')).toBeInTheDocument()
  })

  it('says nothing is open, with no action and no table', async () => {
    answer({ schedule: company({ has_open_items: false, accounts: null, total_balance: null, show_balance: false }) })
    renderSchedule()
    expect(await screen.findByText('Niente da incassare né da pagare nelle prossime 13 settimane.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Aggiungi/ })).not.toBeInTheDocument()
  })
})

describe('CashSchedule, a solo space', () => {
  it('has no outgoing column, no weekly balance and no payables, and lists its taxes apart', async () => {
    answer({
      schedule: company({
        profile: 'solo',
        show_outgoing: false,
        show_balance: false,
        late_payable: null,
        weeks: [week('2026-11-09', '2026-11-15', { out_expected: null, out_forecast: null, net: null, balance_end: null })],
        taxes: [
          { due_item_id: 't-1', tax: 'irpef', description: 'Saldo 2025', due_date: '2026-11-30', state: 'expected', amount: '2100.00' },
          { due_item_id: 't-2', tax: 'inps', description: null, due_date: '2027-02-16', state: 'forecast', amount: '900.00' },
        ],
        alerts: [],
      }),
    })
    renderSchedule()
    const table = await screen.findByRole('table', { name: 'Le settimane' })
    const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent)
    expect(headers).toEqual(['Settimana', 'Entrate'])
    expect(screen.queryByRole('group', { name: 'In ritardo da pagare' })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Da pagare' })).not.toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/cash/payables', expect.anything())

    const taxes = screen.getByRole('region', { name: 'Le tue tasse' })
    expect(taxes).toHaveTextContent('non tolte dal saldo')
    expect(taxes).toHaveTextContent('IRPEF')
    expect(taxes).toHaveTextContent('Contributi INPS')
    expect(taxes).toHaveTextContent('Previsto')
    expect(taxes).toHaveTextContent(/2\.100,00/)
  })
})
