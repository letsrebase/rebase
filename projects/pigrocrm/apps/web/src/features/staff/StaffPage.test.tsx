/**
 * «Persone dell'azienda» (REB-1027, the approved brief of 2026-11-10): each person's cost, cash
 * and state for the month worked, the month's one F24 line, the panel with the payslip beside the
 * estimate and the hourly cost, and the states the brief draws. Nothing is computed here: the page
 * formats what the server sent, and says why when it sent nothing.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { StaffPage } from './StaffPage'
import { validateStaffSearch } from './search'
import { shiftMonth } from './labels'

const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => session.role === 'admin',
  useAuth: () => ({ user: { id: 'u-1', role: session.role, name: 'Admin' } }),
}))
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), PUT: vi.fn() } }
})
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({
  useSearch: () => ({ month: '2026-10' }),
  useNavigate: () => navigate,
}))
vi.mock('@rebase/ui/use-media-query', () => ({ useMediaQuery: () => true }))

const ENTITY = 'le-1'
const legal: LegalEntityValue = {
  legal_entities: [{ id: ENTITY, name: 'Studio' } as never],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  loaded: true,
  byId: () => undefined,
}

const member = (id: string, name: string, overrides: Record<string, unknown> = {}) => ({
  id,
  legal_entity_id: ENTITY,
  kind: 'employee',
  name,
  user_id: null,
  start_date: '2026-01-01',
  end_date: null,
  hours_per_month: '160.00',
  pay_day: null,
  pay_month_offset: null,
  effective_pay_day: { pay_day: 10, pay_month_offset: 1, source: 'legal_entity' },
  collective_agreement: 'Commercio',
  contract_level: '4',
  gross_annual_pay: '30000.00',
  monthly_payments: 14,
  part_time_percent: '100.00',
  employer_inps_rate: '30.00',
  employer_inail_rate: '1.00',
  employee_inps_rate: '9.19',
  thirteenth_month: 12,
  fourteenth_month: 6,
  annual_fee: null,
  fee_payment: null,
  accrues_tfm: false,
  tfm_annual_amount: null,
  inps_company_share: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
})

const cost = (total: string) => ({ gross: '2500.00', employer_inps: '750.00', employer_inail: '25.00', total })
const item = (kind: string, amount: string, due_date: string) => ({ kind, amount, due_date, payroll_month: '2026-10-01' })

const GIULIA = member('m-1', 'Giulia Bianchi', { user_id: 'u-9' })
const MARCO = member('m-2', 'Marco Rossi', { collective_agreement: null, contract_level: null })

const month = (members: unknown[]) => ({
  legal_entity_id: ENTITY,
  month: '2026-10-01',
  members,
  cost_total: '6550.00',
  cash_total: '0.00',
})

const GIULIA_PAYSLIP = {
  staff_member_id: 'm-1',
  name: 'Giulia Bianchi',
  kind: 'employee',
  source: 'payslip',
  cost: cost('3275.00'),
  cash: [item('net_pay', '1742.00', '2026-11-10'), item('withholding', '321.43', '2026-11-16')],
  not_estimated: [],
}
const MARCO_ESTIMATE = {
  staff_member_id: 'm-2',
  name: 'Marco Rossi',
  kind: 'employee',
  source: 'estimate',
  cost: cost('3275.00'),
  cash: [],
  not_estimated: ['accepted_payslip'],
}

let answers: Record<string, unknown>

function respond() {
  vi.mocked(api.GET).mockImplementation((async (path: string) => {
    if (path in answers) {
      const value = answers[path]
      return value instanceof Error ? { error: { detail: value.message }, response: new Response(null, { status: 422 }) } : { data: value, response: new Response(null, { status: 200 }) }
    }
    return { data: {}, response: new Response(null, { status: 200 }) }
  }) as never)
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext.Provider value={legal}>
        <StaffPage />
      </LegalEntityContext.Provider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  session.role = 'admin'
  navigate.mockReset()
  vi.mocked(api.POST).mockReset()
  answers = {
    '/api/staff': { items: [GIULIA, MARCO], next_cursor: null },
    '/api/staff/payroll/{legal_entity_id}': month([GIULIA_PAYSLIP, MARCO_ESTIMATE]),
    '/api/staff/pay-days/{legal_entity_id}': { legal_entity_id: ENTITY, pay_day: 10, pay_month_offset: 1 },
    '/api/legal-entities/{legal_entity_id}/fiscal-profile': { pack_id: 'it-standard' },
    '/api/users/directory': [],
    '/api/staff/{staff_member_id}/hourly-cost': new Error('la persona non e\' collegata a un utente'),
  }
  respond()
})
afterEach(() => vi.clearAllMocks())

describe('«Persone dell\'azienda»', () => {
  it('draws each person with cost, cash and state, and every amount says what it is', async () => {
    renderPage()
    const table = await screen.findByRole('table')
    const giulia = within(table).getByText('Giulia Bianchi').closest('tr')!
    expect(within(giulia).getByText('Cedolino accettato')).toBeInTheDocument()
    expect(within(giulia).getAllByText('Cedolino').length).toBeGreaterThanOrEqual(2)
    expect(within(giulia).getByText(/il 10\/11\/2026/)).toBeInTheDocument()
    expect(within(giulia).getByText('Accettato')).toBeInTheDocument()

    const marco = within(table).getByText('Marco Rossi').closest('tr')!
    expect(within(marco).getByText('Stima')).toBeInTheDocument()
    expect(within(marco).getByText('Stimato')).toBeInTheDocument()
    // No net pay without a first accepted payslip: a dash and the server's reason, never a guess.
    expect(within(marco).getAllByText('Serve il primo cedolino accettato').length).toBeGreaterThan(0)
  })

  it('names the month\'s state after the least advanced person, and who holds it', async () => {
    renderPage()
    await screen.findByRole('table')
    expect(screen.getByText('Stima, 1 su 2 da cedolino')).toBeInTheDocument()
    expect(screen.getByText(/Lo tiene a «Stimato»: Marco Rossi/)).toBeInTheDocument()
  })

  it('gives the F24 no amount while anyone lacks an accepted payslip, and the reason', async () => {
    renderPage()
    await screen.findByRole('table')
    const f24 = screen.getByLabelText('F24 del mese')
    expect(f24).toHaveTextContent('il 16/11/2026')
    expect(f24).toHaveTextContent('Serve il cedolino accettato di Marco Rossi')
    expect(f24).not.toHaveTextContent('€')
  })

  it('gives the F24 its amount once every person has an accepted payslip', async () => {
    answers['/api/staff/payroll/{legal_entity_id}'] = month([
      { ...GIULIA_PAYSLIP, cash: [...GIULIA_PAYSLIP.cash, item('inps_contributions', '750.00', '2026-11-16')] },
    ])
    answers['/api/staff'] = { items: [GIULIA], next_cursor: null }
    renderPage()
    await screen.findByRole('table')
    expect(screen.getByLabelText('F24 del mese')).toHaveTextContent('1.071,43')
  })

  it('opens a person: the payslip is accepted with typed figures, by the same route as ever', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: {}, response: new Response(null, { status: 201 }) } as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Marco Rossi'))
    const panel = await screen.findByLabelText('Persona aperta')
    await user.type(within(panel).getByLabelText('Lordo (€)'), '2500,00')
    await user.type(within(panel).getByLabelText('Netto (€)'), '1850.50')
    await user.type(within(panel).getByLabelText('Ritenute IRPEF (€)'), '300')
    await user.click(within(panel).getByRole('button', { name: 'Accetta il cedolino' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/staff/{staff_member_id}/payslips', {
        params: { path: { staff_member_id: 'm-2' } },
        body: { period: '2026-10-01', gross: '2500.00', net: '1850.50', withholding: '300' },
      }),
    )
  })

  it('never carries figures typed for one person over to another', async () => {
    answers['/api/staff'] = { items: [MARCO, member('m-5', 'Anna Verdi')], next_cursor: null }
    answers['/api/staff/payroll/{legal_entity_id}'] = month([MARCO_ESTIMATE, { ...MARCO_ESTIMATE, staff_member_id: 'm-5', name: 'Anna Verdi' }])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Marco Rossi', { selector: 'p' }))
    const panel = await screen.findByLabelText('Persona aperta')
    await user.type(within(panel).getByLabelText('Lordo (€)'), '2500')
    await user.click(screen.getByText('Anna Verdi', { selector: 'p' }))
    expect(within(await screen.findByLabelText('Persona aperta')).getByLabelText('Lordo (€)')).toHaveValue('')
  })

  it('shows the error, not a table of people who seem to be out of service, when the month fails', async () => {
    answers['/api/staff/payroll/{legal_entity_id}'] = new Error('Non riesco a stimare il mese')
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Non riesco a stimare il mese')
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByText('Non in servizio')).toBeNull()
  })

  it('offers no payslip form for a month a payslip already fixed', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Giulia Bianchi'))
    const panel = await screen.findByLabelText('Persona aperta')
    expect(within(panel).queryByRole('button', { name: 'Accetta il cedolino' })).toBeNull()
    expect(within(panel).getByText('Solo amministratori')).toBeInTheDocument()
    expect(within(panel).getAllByText('digitata').length).toBe(3)
  })

  it('shows the hourly cost the server offers and adopts that figure only on the button', async () => {
    answers['/api/staff/{staff_member_id}/hourly-cost'] = {
      staff_member_id: 'm-1',
      user_id: 'u-9',
      month: '2026-11-01',
      monthly_cost: '3275.00',
      hours_per_month: '160.00',
      hourly_cost: '20.47',
      current_hourly_cost: null,
    }
    vi.mocked(api.POST).mockResolvedValue({ data: {}, response: new Response(null, { status: 200 }) } as never)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Giulia Bianchi'))
    const panel = await screen.findByLabelText('Persona aperta')
    expect(await within(panel).findByText('20,47 €/h')).toBeInTheDocument()
    expect(within(panel).getByText(/Non viene scritto finché non lo adotti/)).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
    await user.click(within(panel).getByRole('button', { name: 'Adotta il costo orario' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/staff/{staff_member_id}/hourly-cost', {
        params: { path: { staff_member_id: 'm-1' } },
        body: { hourly_cost: '20.47' },
      }),
    )
  })

  it('says why there is no hourly cost in the server\'s own words', async () => {
    answers['/api/staff/{staff_member_id}/hourly-cost'] = new Error('la persona non e\' collegata a un utente')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Marco Rossi'))
    const panel = await screen.findByLabelText('Persona aperta')
    expect(await within(panel).findByText(/non e' collegata a un utente/)).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Adotta il costo orario' })).toBeNull()
  })

  it('says what lives here and offers the one action when there is nobody', async () => {
    answers['/api/staff'] = { items: [], next_cursor: null }
    answers['/api/staff/payroll/{legal_entity_id}'] = month([])
    renderPage()
    expect(await screen.findByText('Nessuna persona ancora.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Aggiungi una persona' }).length).toBeGreaterThan(0)
  })

  it('says nothing about pay to anyone but an administrator and asks for nothing', () => {
    session.role = 'collaborator'
    renderPage()
    expect(screen.getByText('I compensi li vedono solo gli amministratori.')).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toMatch(/€/)
  })

  it('shows the server\'s sentence when the month cannot be read', async () => {
    answers['/api/staff'] = new Error('Impossibile leggere le persone')
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Impossibile leggere le persone')
    expect(screen.queryByRole('table')).toBeNull()
  })
})

describe('the month in the URL', () => {
  it('reads only a well-formed month and the ended flag', () => {
    expect(validateStaffSearch({ month: '2026-10', ended: true })).toEqual({ month: '2026-10', ended: true })
    expect(validateStaffSearch({ month: '2026-13' })).toEqual({ month: undefined, ended: undefined })
    expect(validateStaffSearch({ month: 'ottobre' }).month).toBeUndefined()
  })

  it('steps across a year', () => {
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
  })
})
