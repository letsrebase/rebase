import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { FeeOccurrencesSection } from './FeeOccurrencesSection'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
  Link: ({ children, params }: { children: React.ReactNode; params: { invoiceId: string } }) => (
    <a href={`/app/invoices/${params.invoiceId}`}>{children}</a>
  ),
}))
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
const auth = vi.hoisted(() => ({ canBill: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.canBill }))

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const CARD = 'card-1'

function occurrence(overrides: Record<string, unknown>) {
  return {
    rate_card_id: CARD,
    period_start: '2026-11-10',
    period_end: '2026-12-09',
    delivery_period: 'monthly',
    amount: '1200.00',
    state: 'to_bill',
    invoice: null,
    ...overrides,
  }
}

const ISSUED = occurrence({
  period_start: '2026-09-10',
  period_end: '2026-10-09',
  state: 'issued',
  invoice: {
    id: 'inv-issued',
    type: 'invoice',
    status: 'issued',
    year: 2026,
    number: 12,
    reference: null,
  },
})
const DRAFTED = occurrence({
  period_start: '2026-10-10',
  period_end: '2026-11-09',
  state: 'drafted',
  invoice: { id: 'inv-draft', type: 'invoice', status: 'draft', year: null, number: null, reference: null },
})
const TO_BILL = occurrence({})
const NEXT = occurrence({ period_start: '2026-12-10', period_end: '2027-01-09' })
const ONE_OFF = occurrence({
  rate_card_id: 'card-2',
  period_start: '2026-12-01',
  period_end: null,
  delivery_period: 'one_off',
  amount: '500.00',
})

function fees(overrides: Record<string, unknown> = {}) {
  return {
    contract_id: 'c1',
    contract_status: 'active',
    currency: 'EUR',
    billable: true,
    block_reason: null,
    date_from: '2026-08-12',
    date_to: '2027-05-09',
    occurrences: [ISSUED, DRAFTED, TO_BILL, NEXT],
    ...overrides,
  }
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FeeOccurrencesSection contractId="c1" currency="EUR" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(toast.success).mockReset()
  navigate.mockReset()
  auth.canBill = true
})

describe('FeeOccurrencesSection', () => {
  it('lists each occurrence with where it stands, its period and its amount', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(fees({ occurrences: [ISSUED, DRAFTED, TO_BILL, ONE_OFF] }))) as never)
    renderSection()

    expect(await screen.findByText('Mensile · dal 10/11/2026 al 09/12/2026')).toBeInTheDocument()
    expect(screen.getByText('Una tantum')).toBeInTheDocument()
    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(within(rows[0]!).getByText('Fatturato')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('In bozza')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('Da fatturare')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('1.200,00 €')).toBeInTheDocument()
    expect(within(rows[3]!).getByText('500,00 €')).toBeInTheDocument()
  })

  it('links a billed occurrence to its invoice and offers the button only on the ones to bill', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(fees())) as never)
    renderSection()

    expect(await screen.findByRole('link', { name: '2026/12' })).toHaveAttribute('href', '/app/invoices/inv-issued')
    expect(screen.getByRole('link', { name: 'Bozza di fattura' })).toHaveAttribute('href', '/app/invoices/inv-draft')
    expect(screen.getAllByRole('button', { name: /^Fattura il canone del/ })).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Fattura il canone del 10/11/2026' })).toBeEnabled()
  })

  it('bills the occurrence with its card and its date and opens the draft', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(fees())) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ created: true, invoice: { id: 'inv-new' } }, 201)) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Fattura il canone del 10/11/2026' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/invoice-fee',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' } },
        body: { rate_card_id: CARD, period_start: '2026-11-10' },
      }),
    )
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/invoices/$invoiceId',
        params: { invoiceId: 'inv-new' },
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('Bozza di fattura creata per il canone')
  })

  it('says so when the occurrence was already on a draft and opens that one', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(fees())) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ created: false, invoice: { id: 'inv-draft' } })) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Fattura il canone del 10/11/2026' }))

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/invoices/$invoiceId',
        params: { invoiceId: 'inv-draft' },
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('Il canone è già su questa bozza')
  })

  it('names only the row being billed and stops the other buttons meanwhile', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(fees())) as never)
    vi.mocked(api.POST).mockImplementation((() => new Promise(() => {})) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Fattura il canone del 10/11/2026' }))

    expect(await screen.findByRole('button', { name: 'Fattura il canone del 10/11/2026' })).toHaveTextContent('Creazione…')
    const other = screen.getByRole('button', { name: 'Fattura il canone del 10/12/2026' })
    expect(other).toBeDisabled()
    expect(other).toHaveTextContent('Fattura il canone')
  })

  it('shows the refusal the server gives', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(fees())) as never)
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        { status: 409, code: 'conflict', entity: 'contract', detail: 'contract: il canone di questo periodo è già in una fattura emessa' },
        409,
      )) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Fattura il canone del 10/11/2026' }))

    expect(await screen.findByText(/già in una fattura emessa/)).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('shows the rows without the button to somebody who may not bill', async () => {
    auth.canBill = false
    vi.mocked(api.GET).mockImplementation((() => ok(fees())) as never)
    renderSection()

    expect((await screen.findAllByText('Da fatturare')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /Fattura il canone/ })).not.toBeInTheDocument()
  })

  it('says why a contract cannot be billed and stops the buttons', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
      ok(
        fees({
          contract_status: 'draft',
          billable: false,
          block_reason: 'il contratto è ancora una bozza: i canoni si fatturano da un contratto attivo',
        }),
      )) as never)
    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Il contratto è ancora una bozza: i canoni si fatturano da un contratto attivo.',
    )
    for (const button of screen.getAllByRole('button', { name: /^Fattura il canone del/ })) {
      expect(button).toBeDisabled()
    }
  })

  it('draws nothing for a contract with no recurring fee in the window', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(fees({ occurrences: [] }))) as never)
    const { container } = renderSection()

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('shows the wait and then the error of the read', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
      failed({ status: 500, code: 'internal', detail: 'x' }, 500)) as never)
    renderSection()

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
