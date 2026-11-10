import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ContractDetail } from './ContractDetail'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useParams: () => ({ contractId: 'c1' }),
    useNavigate: () => vi.fn(),
    Link: ({ children }: { children: React.ReactNode }) => <a href="/cliente">{children}</a>,
  }
})

const auth = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.canWrite, useIsAdmin: () => true }))

const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const CONTRACT = {
  id: 'c1',
  customer_id: 'cust-1',
  title: 'Sviluppo piattaforma',
  start: '2026-01-01',
  end: '2026-12-31',
  renewal_type: 'tacit',
  renewal_notice_days: 60,
  termination_notice_days: 30,
  payment_days: 30,
  payment_end_of_month: true,
  billing_cadence: 'monthly',
  currency: 'EUR',
  requires_prior_approval: true,
  applies_social_charge: false,
  customer_share_cap: '50.00',
  expense_policy: { kind: 'reimbursable_with_cap', amount_cap: '500.00' },
  status: 'draft',
  note: null,
  custom_fields: {},
}

// The occurrences the contract's recurring fees draw; a contract with none shows no «Canoni».
let feeOccurrences: unknown[] = []

function mockApi(contract: ReturnType<typeof ok> | ReturnType<typeof failed>) {
  mockGet.mockImplementation(((path: string) => {
    if (path === '/api/contracts/{contract_id}') return contract
    if (path === '/api/organizations/{organization_id}') return ok({ id: 'cust-1', legal_name: 'ACME Srl' })
    if (path === '/api/work-units') return ok({ items: [], truncated: false })
    if (path === '/api/schema/{entity_type}') {
      return ok({ entity_type: 'contract', native_fields: [], custom_fields: [] })
    }
    if (path.endsWith('/rebase-engagement')) {
      return failed({ status: 404, code: 'not_found', detail: 'x' }, 404)
    }
    if (path.endsWith('/renewal-assumption')) {
      return failed({ status: 404, code: 'not_found', entity: 'renewal_assumption', detail: 'x' }, 404)
    }
    if (path.endsWith('/projection')) {
      return ok({
        irrevocability_window_until_to: '2026-11-25',
        scheduled: '0.00',
        from_renewal: '0.00',
        total: '0.00',
      })
    }
    if (path.endsWith('/concentration')) {
      return ok({
        period_from: '2026-01-01',
        period_to: '2026-12-31',
        currencies: [{ currency: 'EUR', customer_revenue: '0.00', total_revenue: '0.00', share: 0 }],
      })
    }
    if (path.endsWith('/fee-occurrences')) {
      return ok({
        contract_id: 'c1',
        contract_status: 'active',
        currency: 'EUR',
        billable: true,
        block_reason: null,
        date_from: '2026-08-12',
        date_to: '2027-05-09',
        occurrences: feeOccurrences,
      })
    }
    if (path.endsWith('/billable-days')) {
      return ok({
        contract_id: 'c1',
        currency: 'EUR',
        lines: [],
        months: [],
        work_units: 0,
        quantity: '0',
        taxable_amount: '0.00',
        without_rate: [],
        without_approval: 0,
        billable: false,
      })
    }
    if (path.endsWith('/register')) {
      return ok({
        contract_id: 'c1',
        title: 'Sviluppo piattaforma',
        customer: 'ACME Srl',
        from: '2026-10-01',
        to: '2026-10-31',
        entries: [],
        quantity_total: '0',
        without_approval: 0,
      })
    }
    return ok([])
  }) as never)
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ContractDetail />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  feeOccurrences = []
  auth.canWrite = true
})

describe('ContractDetail', () => {
  it('states the terms, the billing rules and the three sections of the page', async () => {
    mockApi(ok(CONTRACT))
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Sviluppo piattaforma' })).toBeInTheDocument()
    expect(screen.getByText('Bozza')).toBeInTheDocument()
    expect(screen.getByText('Rinnovo tacito')).toBeInTheDocument()
    expect(screen.getByText('60 giorni')).toBeInTheDocument()
    expect(screen.getByText('30 giorni fine mese')).toBeInTheDocument()
    expect(screen.getByText('Rimborsate fino a un tetto, tetto 500,00 €')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Tariffe' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Spese' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Giornate da fatturare' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Registro giornate' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Rinnovo e ricavi programmati' })).toBeInTheDocument()
    // The contract's days (REB-745): its own section, with the empty state of a contract that has none.
    expect(screen.getByRole('heading', { name: 'Giornate' })).toBeInTheDocument()
    expect(await screen.findByText('Nessuna giornata su questo contratto')).toBeInTheDocument()
  })

  it('draws the fee occurrences only for a contract that has a recurring fee', async () => {
    mockApi(ok(CONTRACT))
    const without = renderPage()
    expect(await screen.findByRole('heading', { name: 'Tariffe' })).toBeInTheDocument()
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/api/contracts/{contract_id}/fee-occurrences', expect.anything()))
    expect(screen.queryByRole('heading', { name: 'Canoni' })).not.toBeInTheDocument()
    without.unmount()

    feeOccurrences = [
      {
        rate_card_id: 'card-1',
        period_start: '2026-11-10',
        period_end: '2026-12-09',
        delivery_period: 'monthly',
        amount: '1200.00',
        state: 'to_bill',
        invoice: null,
      },
    ]
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Canoni' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Fattura il canone del 10/11/2026' })).toBeInTheDocument()
  })

  it('shows the cap on the customer’s share in the billing card, or says there is none', async () => {
    mockApi(ok(CONTRACT))
    const first = renderPage()
    expect(await screen.findByText('Tetto sulla quota')).toBeInTheDocument()
    expect(screen.getByText('Tetto sulla quota').parentElement).toHaveTextContent('50%')
    first.unmount()

    mockApi(ok({ ...CONTRACT, customer_share_cap: null }))
    renderPage()
    expect(await screen.findByText('Nessuno')).toBeInTheDocument()
    expect(screen.getByText('Tetto sulla quota').parentElement).toHaveTextContent('Nessuno')
  })

  it('offers Attiva, Stato and Modifica to a writer', async () => {
    mockApi(ok(CONTRACT))
    renderPage()
    expect(await screen.findByRole('button', { name: 'Attiva' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stato' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Modifica' })).toBeInTheDocument()
    // This contract requires prior approval, so the days section offers to ask for one.
    expect(screen.getByRole('button', { name: 'Chiedi approvazione' })).toBeInTheDocument()
  })

  it('offers no write control to someone who cannot write', async () => {
    auth.canWrite = false
    mockApi(ok(CONTRACT))
    renderPage()
    await screen.findByRole('heading', { name: 'Sviluppo piattaforma' })
    expect(screen.queryByRole('button', { name: 'Attiva' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifica' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nuova tariffa' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nuova spesa' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Salva assunzione' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Chiedi approvazione' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Registra giornata/ })).not.toBeInTheDocument()
  })

  it('says the contract does not exist on a 404 and does not blame the network', async () => {
    mockApi(failed({ status: 404, code: 'not_found', detail: 'contract x not found' }, 404))
    renderPage()
    expect(await screen.findByText('Contratto non trovato.')).toBeInTheDocument()
  })
})
