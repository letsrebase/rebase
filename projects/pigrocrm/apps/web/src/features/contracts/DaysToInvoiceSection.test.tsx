import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { DaysToInvoiceSection } from './DaysToInvoiceSection'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
const auth = vi.hoisted(() => ({ canBill: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.canBill }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const OCTOBER = {
  month: '2026-10',
  description: 'Attività ottobre 2026 - 1.50 giorno',
  work_units: 2,
  quantity: '1.50',
  unit_of_measure: 'day',
  unit_price: '500.00',
  amount: '750.00',
}
const NOVEMBER = {
  month: '2026-11',
  description: 'Attività novembre 2026 - 1.00 giorno',
  work_units: 1,
  quantity: '1.00',
  unit_of_measure: 'day',
  unit_price: '500.00',
  amount: '500.00',
}

function days(overrides: Record<string, unknown> = {}) {
  return {
    contract_id: 'c1',
    currency: 'EUR',
    lines: [OCTOBER, NOVEMBER],
    months: ['2026-10', '2026-11'],
    work_units: 3,
    quantity: '2.50',
    taxable_amount: '1250.00',
    without_rate: [],
    without_approval: 0,
    billable: true,
    ...overrides,
  }
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <DaysToInvoiceSection contractId="c1" currency="EUR" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  navigate.mockReset()
  auth.canBill = true
})

describe('DaysToInvoiceSection', () => {
  it('shows the ready days as the lines the draft would carry, priced by the server', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(days())) as never)
    renderSection()

    expect(await screen.findByText('Attività ottobre 2026 - 1,5 giorno')).toBeInTheDocument()
    expect(screen.getByText('2 giornate a 500,00 € per giorno')).toBeInTheDocument()
    expect(screen.getByText('750,00 €')).toBeInTheDocument()
    expect(screen.getByText('1.250,00 €')).toBeInTheDocument()
  })

  it('creates the draft with every ready day and opens it', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(days())) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'inv-1' })) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Crea la bozza di fattura' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/invoice-days',
      expect.objectContaining({ params: { path: { contract_id: 'c1' } }, body: {} }),
    )
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/invoices/$invoiceId',
        params: { invoiceId: 'inv-1' },
      }),
    )
  })

  it('bills one month when one is chosen, asking the server for that month', async () => {
    vi.mocked(api.GET).mockImplementation(((_path: string, init: { params: { query?: { from?: string } } }) =>
      ok(init.params.query?.from === '2026-10-01' ? days({ lines: [OCTOBER], months: ['2026-10', '2026-11'], taxable_amount: '750.00' }) : days())) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'inv-1' })) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('combobox', { name: 'Periodo' }))
    await userEvent.click(await screen.findByRole('option', { name: 'ottobre 2026' }))
    await waitFor(() => expect(screen.queryByText('Attività novembre 2026 - 1 giorno')).not.toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Crea la bozza di fattura' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/invoice-days',
      expect.objectContaining({ body: { from: '2026-10-01', to: '2026-10-31' } }),
    )
  })

  it('stops the button and names the days no rate card covers', async () => {
    vi.mocked(api.GET).mockImplementation(
      (() => ok(days({ without_rate: ['2026-03-05'], billable: false }))) as never,
    )
    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('05/03/2026')
    expect(screen.getByRole('button', { name: 'Crea la bozza di fattura' })).toBeDisabled()
  })

  it('still offers the priced month when another month holds days with no rate card', async () => {
    vi.mocked(api.GET).mockImplementation(
      (() =>
        ok(
          days({
            lines: [OCTOBER],
            months: ['2026-10'],
            taxable_amount: '750.00',
            without_rate: ['2026-11-03'],
            billable: false,
          }),
        )) as never,
    )
    renderSection()

    // Without the picker the whole set is the only choice, and the missing November rate
    // would block billing October too.
    expect(await screen.findByRole('combobox', { name: 'Periodo' })).toBeInTheDocument()
  })

  it('says why a contract in another currency cannot be billed from here', async () => {
    vi.mocked(api.GET).mockImplementation(
      (() => ok(days({ billable: false, block_reason: 'il contratto è in USD: le fatture si emettono in EUR' }))) as never,
    )
    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('Il contratto è in USD')
    expect(screen.getByRole('button', { name: 'Crea la bozza di fattura' })).toBeDisabled()
  })

  it('says how many days wait for an approval, and offers nothing when none is ready', async () => {
    vi.mocked(api.GET).mockImplementation(
      (() => ok(days({ lines: [], months: [], work_units: 0, taxable_amount: '0.00', without_approval: 2, billable: false }))) as never,
    )
    renderSection()

    expect(await screen.findByText('Nessuna giornata pronta da fatturare.')).toBeInTheDocument()
    expect(screen.getByText(/2 giornate lavorate senza l’approvazione/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Crea la bozza di fattura' })).not.toBeInTheDocument()
  })

  it('shows the lines to someone who cannot bill but not the button', async () => {
    auth.canBill = false
    vi.mocked(api.GET).mockImplementation((() => ok(days())) as never)
    renderSection()

    expect(await screen.findByText('Attività ottobre 2026 - 1,5 giorno')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Crea la bozza di fattura' })).not.toBeInTheDocument()
  })
})
