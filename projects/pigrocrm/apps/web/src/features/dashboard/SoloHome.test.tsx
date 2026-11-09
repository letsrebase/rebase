/**
 * The Home of a solo space (spec 2026-11-09 B5, REB-958): what to invoice, when the money
 * comes in, the active contracts and how long they run, and the flat-rate figures only on
 * the flat-rate pack. The queries are the ones the company tabs use; here the question is
 * what the page draws from their answers and what it leaves out when a module is off.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { spaceModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'
import { queryKeys } from '@/lib/query'
import { SoloHome } from './SoloHome'

vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: 'admin' } }),
    useCan: (action: Parameters<typeof can>[1]) => can('admin', action),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  linkOptions: <T,>(options: T) => options,
  Link: ({ to, children, params }: { to: string; children: React.ReactNode; params?: Record<string, string> }) => (
    <a href={params ? Object.entries(params).reduce((path, [key, value]) => path.replace(`$${key}`, value), to) : to}>
      {children}
    </a>
  ),
}))

const contract = (id: string, title: string, end: string | null) => ({
  id,
  title,
  end,
  customer_id: 'c-1',
  legal_entity_id: 'le-1',
  status: 'active',
  currency: 'EUR',
  customer_share_cap: null,
})

const READY = {
  contract_id: 'k-1',
  currency: 'EUR',
  lines: [{}],
  months: ['2026-10'],
  work_units: 3,
  quantity: '3',
  taxable_amount: '2400.00',
  without_rate: [],
  without_approval: 1,
  billable: true,
}
const NOTHING_READY = { ...READY, contract_id: 'k-2', lines: [], months: [], work_units: 0, taxable_amount: '0.00', without_approval: 0, billable: false }

const RECEIVABLES = {
  calculated_at: '2026-11-09T10:00:00Z',
  today: '2026-11-09',
  total: '5000.00',
  bands: [{ code: 'expired', label: 'Scaduto', date_from: null, date_to: null, amount: '1200.00', number: 2, share: 1, link: '/x' }],
  by_month: [{ month: '2026-12-01', amount: '3800.00', number: 1, share: 1 }],
  by_customer: [],
  overdue: [],
  total_overdue: 2,
}

const CEILINGS = {
  year: 2026,
  legal_entity_id: 'le-1',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  thresholds: [
    { id: 't1', label: 'Soglia 85.000', threshold: '85000.00', revenue: '40000.00', remaining: '45000.00', usage: 0.47, exceeded: false, alert_level: null, consequence: 'leave_next_year' },
  ],
}
const ESTIMATE = {
  year: 2026,
  estimate: true,
  warning: 'Stima indicativa.',
  revenue: '40000.00',
  profitability_coefficient: '0.78',
  taxable_amount: '31200.00',
  substitute_tax_rate: '0.05',
  substitute_tax: '1560.00',
  social_security_rate: '0.2607',
  contributions: '8133.84',
  estimated_net_income: '30000.00',
  due_total: '9693.84',
}
const TODAY_TASKS = {
  today: '2026-11-09',
  overdue: { total: 0, items: [] },
  today_due: { total: 0, items: [] },
  next_seven_days: { total: 0, items: [] },
  without_due_date: { total: 0, items: [] },
}

let answers: Record<string, unknown>

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) })
}

function renderHome(off: readonly ModuleKey[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(queryKeys.modules, spaceModules(off, 'solo'))
  render(
    <QueryClientProvider client={client}>
      <SoloHome />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.useRealTimers()
  answers = {
    '/api/contracts': { items: [contract('k-1', 'Contratto Acme', '2026-12-31'), contract('k-2', 'Contratto Beta', null)], next_cursor: null },
    '/api/dashboard/receivables': RECEIVABLES,
    '/api/analytics/ceilings': CEILINGS,
    '/api/analytics/fiscal': ESTIMATE,
    '/api/reminders': [],
    '/api/activities/today': TODAY_TASKS,
    '/api/legal-entities': [],
  }
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string, options?: { params?: { path?: { contract_id?: string } } }) => {
    if (path === '/api/contracts/{contract_id}/billable-days') {
      return ok(options?.params?.path?.contract_id === 'k-1' ? READY : NOTHING_READY)
    }
    return ok(answers[path])
  }) as never)
})

describe('SoloHome', () => {
  it('says what is ready to invoice, per contract, with the amount the draft will carry', async () => {
    renderHome()
    const section = (await screen.findByRole('heading', { name: 'Da fatturare ora' })).closest('section')!
    expect(await within(section).findByText('2.400,00 €')).toBeInTheDocument()
    expect(within(section).getByText(/3 giornate pronte da fatturare/)).toBeInTheDocument()
    expect(within(section).getByText(/1 giornata aspetta l’approvazione/)).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'Contratto Acme' })).toHaveAttribute('href', '/app/contracts/k-1')
    // The contract with nothing ready has no row here.
    expect(within(section).queryByText('Contratto Beta')).toBeNull()
  })

  it('lists a fixed fee whose period closed without an invoice, from the reminders', async () => {
    answers['/api/reminders'] = [
      { key: 'r1', type: 'fixed_fee_period_unbilled', label: 'Canone del periodo da fatturare', severity: 'serious', title: 'Canone del periodo da fatturare: Contratto Acme', detail: '', entity: 'contract', entity_id: 'k-1', customer_id: 'c-1', status: 'open' },
      { key: 'r2', type: 'task_overdue', label: 'Attività scaduta', severity: 'warning', title: 'Attività scaduta: x', detail: '', entity: 'task', entity_id: 't', customer_id: null, status: 'open' },
    ]
    renderHome()
    const section = (await screen.findByRole('heading', { name: 'Da fatturare ora' })).closest('section')!
    expect(await within(section).findByText('Canone del periodo da fatturare')).toBeInTheDocument()
    expect(within(section).queryByText(/Attività scaduta/)).toBeNull()
  })

  it('does not list a fixed fee of a contract the list does not hold', async () => {
    answers['/api/reminders'] = [
      { key: 'r9', type: 'fixed_fee_period_unbilled', label: 'Canone del periodo da fatturare', severity: 'serious', title: 'Canone del periodo da fatturare: Altro', detail: '', entity: 'contract', entity_id: 'elsewhere', customer_id: null, status: 'open' },
    ]
    renderHome()
    const section = (await screen.findByRole('heading', { name: 'Da fatturare ora' })).closest('section')!
    await within(section).findByText('Contratto Acme')
    expect(within(section).queryByText('Altro')).toBeNull()
  })

  it('says why a contract cannot be invoiced, and prints no amount for it', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/contracts/{contract_id}/billable-days'
        ? ok({ ...READY, billable: false, without_rate: ['2026-10-02'] })
        : ok(answers[path])) as never)
    renderHome()
    expect(await screen.findAllByText(/non ha una tariffa in vigore/)).not.toHaveLength(0)
    expect(screen.queryByText('2.400,00 €')).toBeNull()
  })

  it('never says there is nothing to invoice over a read that failed', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/reminders') {
        return Promise.resolve({ error: { detail: 'boom' }, response: new Response(null, { status: 500 }) })
      }
      return path === '/api/contracts/{contract_id}/billable-days' ? ok(NOTHING_READY) : ok(answers[path])
    }) as never)
    renderHome()
    await screen.findByRole('heading', { name: 'Da fatturare ora' })
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.queryByText('Niente da fatturare adesso.')).toBeNull()
  })

  it('shows the app loader while the figures are on their way', () => {
    vi.mocked(api.GET).mockImplementation((() => new Promise(() => {})) as never)
    renderHome()
    expect(document.querySelector('[data-slot="loader"]')).not.toBeNull()
  })

  it('says so when nothing is ready', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/contracts/{contract_id}/billable-days' ? ok(NOTHING_READY) : ok(answers[path])) as never)
    renderHome()
    expect(await screen.findByText('Niente da fatturare adesso.')).toBeInTheDocument()
  })

  it('draws the receivables schedule: what is owed, what is late, the months', async () => {
    renderHome()
    const owed = await screen.findByRole('group', { name: 'Da incassare' })
    expect(within(owed).getByText('5.000,00 €')).toBeInTheDocument()
    const late = screen.getByRole('group', { name: 'Di cui scaduto' })
    expect(within(late).getByText('1.200,00 €')).toBeInTheDocument()
    expect(within(late).getByText('2 fatture scadute')).toBeInTheDocument()
    expect(screen.getByText('Incassi attesi per mese di scadenza')).toBeInTheDocument()
  })

  it('lists the active contracts with how long each runs', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 10, 9, 12, 0))
    renderHome()
    const section = (await screen.findByRole('heading', { name: 'Contratti attivi' })).closest('section')!
    expect(await within(section).findByText('fino al 31/12/2026 · 52 giorni')).toBeInTheDocument()
    expect(within(section).getByText('senza scadenza')).toBeInTheDocument()
  })

  it('draws the ceilings and the tax estimate on the flat-rate pack', async () => {
    renderHome()
    expect(await screen.findByRole('group', { name: 'Soglia 85.000' })).toBeInTheDocument()
    expect(await screen.findByText(/Stima fiscale/)).toBeInTheDocument()
    // The design lists the distance and the estimate, not the «ci sta?» simulator.
    expect(screen.queryByText('Ci sta?')).toBeNull()
    expect(api.GET).toHaveBeenCalledWith('/api/analytics/fiscal', expect.anything())
  })

  it('draws neither on a pack with no ceilings, and never asks for the estimate', async () => {
    answers['/api/analytics/ceilings'] = { ...CEILINGS, pack_id: 'it-standard', thresholds: [] }
    renderHome()
    await screen.findByRole('heading', { name: 'Quando arrivano i soldi' })
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/analytics/ceilings', expect.anything()))
    expect(screen.queryByText(/Soglie del forfettario/)).toBeNull()
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/fiscal', expect.anything())
  })

  it('leaves out the contracts sections and reads no contract with `contracts` off', async () => {
    renderHome(['contracts'])
    await screen.findByRole('heading', { name: 'Quando arrivano i soldi' })
    expect(screen.queryByRole('heading', { name: 'Da fatturare ora' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Contratti attivi' })).toBeNull()
    expect(api.GET).not.toHaveBeenCalledWith('/api/contracts', expect.anything())
  })

  it('leaves out the ceilings and the estimate, and asks for neither, with `taxes` off', async () => {
    renderHome(['taxes'])
    await screen.findByRole('heading', { name: 'Quando arrivano i soldi' })
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/ceilings', expect.anything())
    expect(api.GET).not.toHaveBeenCalledWith('/api/analytics/fiscal', expect.anything())
  })
})
