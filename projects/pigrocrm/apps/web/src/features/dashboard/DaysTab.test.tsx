import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { DaysTab } from './DaysTab'
import { days } from './format'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

const Statuses = [
  'proposed',
  'approved',
  'worked',
  'invoiced',
  'paid',
  'worked_without_approval',
  'disputed',
  'non_billable',
]

function month(yearMonth: string, over: Record<string, unknown> = {}, given: Record<string, string> = {}) {
  return {
    month: yearMonth,
    complete: true,
    by_status: Statuses.map((status) => ({
      status,
      days: given[status] ?? '0.00',
      share: given[status] ? 0.5 : 0,
    })),
    total: Object.values(given).length ? '12.50' : '0.00',
    total_share: 1,
    register: '12.00',
    register_share: 1,
    media_previous: '10.00',
    without_approval: '2.50',
    share_without_approval: 1,
    ...over,
  }
}

const analysis = (over: Record<string, unknown> = {}) => ({
  from: '2026-08-01',
  to: '2026-09-30',
  legal_entity_id: null,
  today: '2026-10-26',
  months: [
    month('2026-08', {}, { worked: '10.00', proposed: '2.50' }),
    month('2026-09', { register: '14.00', media_previous: '11.50' }, { invoiced: '12.50' }),
  ],
  last_complete_month: '2026-09',
  days_in_period: '25.00',
  invoiced_days: '12.50',
  actual_rate: '481.62',
  by_customer: [
    {
      id: 'c1',
      label: 'Acme S.r.l.',
      invoiced_days: '12.50',
      invoiced_amount: '6020.25',
      actual_rate: '481.62',
      days_without_list_price: '0.00',
      nominal_rate: '500.00',
    },
  ],
  by_contract: [
    {
      id: 'k1',
      label: 'Consulenza Acme',
      invoiced_days: '12.50',
      invoiced_amount: '6020.25',
      actual_rate: '481.62',
      days_without_list_price: '1.00',
      nominal_rate: null,
    },
  ],
  without_approval_in_period: '4.50',
  without_approval_always: '9.50',
  ...over,
})

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

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext value={ONE}>
        <DaysTab period={{ from: '2026-08-01', to: '2026-09-30' }} />
      </LegalEntityContext>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('days', () => {
  it('prints days with a decimal comma and no trailing zeros', () => {
    expect(days('1.50')).toBe('1,5')
    expect(days('2.00')).toBe('2')
    expect(days('10.00')).toBe('10')
    expect(days('0.25')).toBe('0,25')
  })
})

describe('DaysTab', () => {
  it('asks for the period and shows the month\'s days and the realised rate', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    expect(
      within(await screen.findByRole('group', { name: 'Giornate nel periodo' })).getByText('25'),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('group', { name: 'Tariffa reale al giorno' })).getByText('481,62 €'),
    ).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/days', {
      params: { query: { from: '2026-08-01', to: '2026-09-30' } },
    })
  })

  it('stacks each month by state with the server\'s shares, and names every state', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    const stack = await screen.findByTestId('stack-2026-08')
    const segments = [...stack.querySelectorAll('[data-status]')]
    expect(segments.map((s) => s.getAttribute('data-status'))).toEqual(['proposed', 'worked'])
    expect((segments[0] as HTMLElement).style.width).toBe('50%')
    const legend = screen.getByRole('list', { name: 'Legenda' })
    expect(within(legend).getByText('Lavorata senza approvazione')).toBeInTheDocument()
    expect(within(legend).getByText('Non fatturabile')).toBeInTheDocument()
  })

  it('sets the realised rate against the rate card and counts an uncovered day apart', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    const row = (await screen.findByText('Consulenza Acme')).closest('tr')!
    expect(within(row).getByText('481,62 €')).toBeInTheDocument()
    // No card covers the day the line billed: a dash, not 0,00 €, and the day is counted.
    expect(within(row).getByText('—')).toBeInTheDocument()
    expect(within(row).getByText('1')).toBeInTheDocument()
    const client = screen.getByText('Acme S.r.l.').closest('tr')!
    expect(within(client).getByText('500,00 €')).toBeInTheDocument()
  })

  it('says the last finished month against the three before it', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    expect(
      await screen.findByText(
        'A settembre 2026 hai registrato 14 giornate, contro una media di 11,5 nei tre mesi prima.',
      ),
    ).toBeInTheDocument()
  })

  it('says nothing about a trend while no month has ended', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis({ last_complete_month: null })))
    renderTab()

    await screen.findByText('Giornate registrate e media dei tre mesi prima')
    expect(screen.queryByText(/hai registrato/)).toBeNull()
  })

  it('shows the worked-without-approval history, this period and for ever', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(analysis()))
    renderTab()

    const group = await screen.findByRole('group', { name: 'Senza approvazione' })
    expect(within(group).getByText('4,5')).toBeInTheDocument()
    expect(within(group).getByText(/in tutto 9,5/)).toBeInTheDocument()
    expect(screen.getByText('Giornate senza approvazione per mese')).toBeInTheDocument()
  })

  it('says so, three times, on a space with no days at all', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      ok(
        analysis({
          months: [month('2026-08'), month('2026-09')].map((m) => ({ ...m, total: '0.00' })),
          days_in_period: '0.00',
          invoiced_days: '0.00',
          actual_rate: null,
          by_customer: [],
          by_contract: [],
          without_approval_in_period: '0.00',
          without_approval_always: '0.00',
        }),
      ),
    )
    renderTab()

    expect(await screen.findAllByText('Nessuna giornata nel periodo.')).toHaveLength(2)
    expect(screen.getByText('Nessuna giornata fatturata nel periodo.')).toBeInTheDocument()
    expect(
      screen.getByText('Nessuna giornata è mai stata lavorata senza l’approvazione che il contratto chiede.'),
    ).toBeInTheDocument()
  })
})
