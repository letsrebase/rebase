import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InvoiceDetail } from './InvoiceDetail'
import type { Invoice, InvoiceLine } from './queries'

// The route keeps this component mounted while only `invoiceId` changes (REB-694), so
// the test drives it the same way: one render, then `rerender` with the next id.
let current = 'inv-1'

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ invoiceId: current }),
  useNavigate: () => vi.fn(),
}))
vi.mock('@/lib/auth', () => ({ useCan: () => true }))
vi.mock('@/lib/legalEntity', () => ({
  useLegalEntity: () => ({ several: false, byId: () => undefined }),
}))
// What the page wraps around the editors is not under test here.
vi.mock('@/components/EntityDetailLayout', () => ({
  EntityDetailLayout: ({ overview }: { overview: React.ReactNode }) => <div>{overview}</div>,
}))
vi.mock('@/features/tasks/EntityTask', () => ({ EntityTask: () => null }))
vi.mock('@/features/invoices/InvoiceActions', () => ({ InvoiceActions: () => null }))
vi.mock('@/features/invoices/InvoicePdfPreview', () => ({ InvoicePdfPreview: () => null }))
vi.mock('@/features/invoices/InvoiceStateBadge', () => ({ InvoiceStateBadge: () => null }))
vi.mock('@/features/invoices/PurchaseOrderEditor', () => ({ PurchaseOrderEditor: () => null }))

function draftInvoice(id: string, due: string): Invoice {
  return {
    id,
    type: 'invoice',
    status: 'draft',
    issue_date: '2026-10-01',
    due_date: due,
    accrual_from: null,
    accrual_to: null,
    payment_status: 'unpaid',
    legal_entity_id: 'le-1',
    payment_date: null,
    transmitted_externally_at: null,
    cancelled_at: null,
    cancellation_reason: null,
    purchase_order_number: null,
    taxable_amount: '100.00',
    vat_amount: '0.00',
    stamp_duty: '0.00',
    total: '100.00',
  } as unknown as Invoice
}

function line(id: string, description: string): InvoiceLine {
  return {
    id,
    description,
    quantity: '1.000000',
    unit_of_measure: null,
    unit_price: '100.000000',
    discount_percentage: null,
    discount_amount: null,
    total_price: '100.00',
  } as unknown as InvoiceLine
}

const INVOICES: Record<string, Invoice> = {
  'inv-1': draftInvoice('inv-1', '2026-11-01'),
  'inv-2': draftInvoice('inv-2', '2026-12-15'),
}
const LINES: Record<string, InvoiceLine[]> = {
  'inv-1': [line('l-1', 'Consulenza ottobre')],
  'inv-2': [line('l-2', 'Consulenza novembre')],
}

vi.mock('@/features/invoices/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/invoices/queries')>()
  return {
    ...actual,
    useInvoice: (id: string) => ({ data: INVOICES[id], isLoading: false, isError: false }),
    useInvoiceLines: (id: string) => ({ data: LINES[id], isError: false }),
  }
})

describe('InvoiceDetail', () => {
  it('starts both editors from the invoice now shown when the route moves to another one', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    // A fresh element each time: React skips an identical one, and the id is read from the mock.
    const tree = () => (
      <QueryClientProvider client={client}>
        <InvoiceDetail />
      </QueryClientProvider>
    )
    const view = render(tree())
    expect(screen.getByLabelText('Scadenza')).toHaveValue('2026-11-01')
    expect(screen.getByLabelText('Descrizione')).toHaveValue('Consulenza ottobre')

    // A draft typed on the first invoice must not follow the person to the next one.
    fireEvent.change(screen.getByLabelText('Scadenza'), { target: { value: '2027-01-01' } })
    fireEvent.change(screen.getByLabelText('Descrizione'), { target: { value: 'Bozza non salvata' } })

    current = 'inv-2'
    view.rerender(tree())

    expect(screen.getByLabelText('Scadenza')).toHaveValue('2026-12-15')
    expect(screen.getByLabelText('Descrizione')).toHaveValue('Consulenza novembre')
  })
})
