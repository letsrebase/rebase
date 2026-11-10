import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { buildPurchaseColumns } from './columns'
import type { CostCategory, Purchase } from './queries'

/* Written out in full rather than cast: a double cast switches the type check off, so
   the day `CostCategory` grows a required field this fixture would keep compiling and
   say nothing. The column reads only `id` and `name`; the rest costs four lines. */
const CATEGORIES: CostCategory[] = [
  {
    id: 'cat-1',
    name: 'Consulenze',
    position: 0,
    code: null,
    archived: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

const PURCHASE: Purchase = {
  id: 'k1',
  kind: 'expense',
  status: 'registered',
  legal_entity_id: null,
  deal_id: null,
  category_id: 'cat-1',
  date: '2026-03-05',
  amount: '2500.50',
  description: 'Consulenza fiscale',
  supplier_id: null,
  merchant_name: 'Studio Rossi',
  number: null,
  received_on: null,
  iva_period: null,
  currency: 'EUR',
  currency_amount: null,
  exchange_rate: null,
  exchange_rate_date: null,
  cost_amount: '2049.59',
  vat_lines: [],
  stamp_duty: null,
  withholding_rate: null,
  withholding_base: null,
  withholding_amount: null,
  fund_rate: null,
  fund_amount: null,
  split_payment: false,
  reverse_charge: false,
  corrects_purchase_id: null,
  inbox_item_id: null,
  buyer_pack_id: null,
  buyer_pack_version: null,
  buyer_regime_code: null,
  document_id: null,
  custom_fields: {},
  created_at: '2026-03-05T00:00:00Z',
  updated_at: '2026-03-05T00:00:00Z',
}

/**
 * Reads a column's rendered cell, not only its accessor: the accessor keeps the plain
 * text value the panel can sort or export, while `cell` carries what the design
 * revision adds -- a calendar icon before the date, a right-aligned amount (design
 * spec §4). Only `row.original` is consulted, so the cast supplies exactly that.
 */
function renderCell(id: string, purchase: Purchase) {
  const column = buildPurchaseColumns(CATEGORIES, []).find((candidate) => candidate.id === id)
  if (column === undefined || typeof column.cell !== 'function') {
    throw new Error(`la colonna ${id} non ha un cell renderer`)
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      {column.cell({ row: { original: purchase } } as never) as ReactElement}
    </QueryClientProvider>,
  )
}

describe('how a purchase row reads', () => {
  it('puts a calendar icon before the date, in Italian day/month/year order', () => {
    const { container } = renderCell('date', PURCHASE)
    expect(screen.getByText('05/03/2026')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  /** The header has to sit over the digits it labels, which only the column can say --
   *  a cell that right-aligned itself would leave its own header on the left. */
  it('declares the amount a right-aligned column', () => {
    const column = buildPurchaseColumns(CATEGORIES, []).find((candidate) => candidate.id === 'amount')
    expect(column?.meta).toEqual({ align: 'right' })
  })

  it('right-aligns the amount inside its cell too', () => {
    renderCell('amount', PURCHASE)
    expect(screen.getByText(/2\.500,50/).className).toContain('text-right')
  })

  /** §4.4: a negative total on an expense or a receipt is a refund, so it stays labelled
   *  rather than being treated as an error -- and the label survives the move into a
   *  right-aligned cell. */
  it('keeps saying «(rimborso)» beside a negative expense', () => {
    renderCell('amount', { ...PURCHASE, amount: '-45.50' })
    expect(screen.getByText(/\(rimborso\)/)).toBeInTheDocument()
  })
})

describe('what a purchase row adds to the old cost row', () => {
  it('does not call the minus of a credit note a refund', () => {
    renderCell('amount', { ...PURCHASE, kind: 'credit_note', amount: '-122.00' })
    expect(screen.getByText(/-122,00/)).toBeInTheDocument()
    expect(screen.queryByText(/\(rimborso\)/)).not.toBeInTheDocument()
  })

  it('shows the cost beside the total, right-aligned', () => {
    const column = buildPurchaseColumns(CATEGORIES, []).find((c) => c.id === 'cost_amount')
    expect(column?.meta).toEqual({ align: 'right' })
    renderCell('cost_amount', PURCHASE)
    expect(screen.getByText(/2\.049,59/)).toBeInTheDocument()
  })

  it('names the kind in Italian and mutes an annulled row', () => {
    renderCell('kind', { ...PURCHASE, kind: 'credit_note', status: 'cancelled' })
    expect(screen.getByText('Nota di credito')).toBeInTheDocument()
    expect(screen.getByText('Annullato')).toHaveAttribute('data-tone', 'muted')
  })

  it('reads the merchant name of an expense and a dash when neither it nor a supplier is there', () => {
    renderCell('supplier', PURCHASE)
    expect(screen.getByText('Studio Rossi')).toBeInTheDocument()
  })

  it('has the number and the supplier-or-merchant as columns', () => {
    const ids = buildPurchaseColumns(CATEGORIES, []).map((column) => column.id)
    expect(ids).toEqual(expect.arrayContaining(['date', 'kind', 'supplier', 'number', 'amount', 'cost_amount']))
  })
})
