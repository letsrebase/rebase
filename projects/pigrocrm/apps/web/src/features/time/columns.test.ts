import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import {
  buildTimeEntryColumns,
  formatHoursValue,
  formatMoneyValue,
  formatRateValue,
  statusOf,
  TIME_ENTRY_STATE_TONE,
} from './columns'
import type { TimeEntry } from './queries'

// `Intl.NumberFormat` separates an amount from its currency symbol with U+00A0, never
// an ordinary space -- a price must not wrap between its digits and its sign. Built
// from its code point rather than pasted, because `'2.500,50 €'` typed with a plain
// space compares unequal to what the formatter really returns, and that failure reads
// as a locale bug rather than as the invisible typo it is.
const NBSP = String.fromCharCode(0x00a0)

describe('the three formatters', () => {
  it('forces the thousands separator below five digits', () => {
    // it-IT's default grouping withholds the separator until the integer part has five
    // digits: `Intl.NumberFormat('it-IT').format(2500.5)` renders "2500,50". Every
    // currency surface in this product forces it on for that reason.
    expect(formatMoneyValue('2500.50')).toBe(`2.500,50${NBSP}€`)
    expect(formatHoursValue('1234.50')).toBe('1.234,5')
  })

  it('renders an absent value as a dash, never as zero', () => {
    // `null` is a real, distinct state: an unpriced hour is not a free hour.
    expect(formatMoneyValue(null)).toBe('—')
    expect(formatRateValue(null)).toBe('—')
    expect(formatMoneyValue('0.00')).toBe(`0,00${NBSP}€`)
  })

  it('shows a rate at the precision the column holds', () => {
    // Numeric(12,6): "33,333333 €/h" is not expressible at two places, which is the
    // whole reason the third scale exists. The unit is appended with an ordinary space:
    // "€/h" is a label this code writes itself, not part of what ICU formatted.
    expect(formatRateValue('33.333333')).toBe('33,333333 €/h')
  })
})


const ENTRY: TimeEntry = {
  id: 't1',
  deal_id: 'd1',
  user_id: 'u1',
  date: '2026-03-05',
  hours: '7.50',
  description: 'Sviluppo',
  billable: true,
  applied_rate: '80.00',
  applied_cost: null,
  rate_source: 'deal',
  cost_source: 'absent',
  line_value: '600.00',
  line_cost: null,
  invoice_line_id: null,
  internal_notes: null,
  custom_fields: {},
  created_at: '2026-03-05T00:00:00Z',
  updated_at: '2026-03-05T00:00:00Z',
}

/**
 * Reads a column's rendered cell, not only its accessor: the accessor keeps the plain
 * text value the timesheet can sort or export, while `cell` carries what the design
 * revision adds -- the calendar icon, right-aligned figures, the state as a dotted pill
 * (design spec §4). Only `row.original` is consulted, so the cast supplies exactly that.
 */
function renderCell(id: string, entry: TimeEntry) {
  const column = buildTimeEntryColumns([]).find((candidate) => candidate.id === id)
  if (column === undefined || typeof column.cell !== 'function') {
    throw new Error(`la colonna ${id} non ha un cell renderer`)
  }
  return render(column.cell({ row: { original: entry } } as never) as ReactElement)
}

function column(id: string) {
  const found = buildTimeEntryColumns([]).find((candidate) => candidate.id === id)
  if (found === undefined) throw new Error(`nessuna colonna con id ${id}`)
  return found
}

describe('how an hour reads', () => {
  it('puts a calendar icon before the date', () => {
    const { container } = renderCell('data', ENTRY)
    expect(screen.getByText('05/03/2026')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  /** The header has to sit over the digits it labels, which only the column can say. */
  it('declares hours, rate and value right-aligned columns', () => {
    expect(column('hours').meta).toEqual({ align: 'right' })
    expect(column('applied_rate').meta).toEqual({ align: 'right' })
    expect(column('line_value').meta).toEqual({ align: 'right' })
  })

  it('aligns hours on tabular digits, through the number cell rather than the money one', () => {
    renderCell('hours', ENTRY)
    const cell = screen.getByText('7,5')
    expect(cell.className).toContain('text-right')
    expect(cell.className).toContain('tabular-nums')
  })

  it('keeps the rate beside where it came from, right-aligned', () => {
    renderCell('applied_rate', ENTRY)
    expect(screen.getByText(/dal deal/).className).toContain('text-right')
  })

  it('shows an unpriced rate as the dash rather than as zero', () => {
    renderCell('applied_rate', { ...ENTRY, applied_rate: null })
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

/**
 * The one status in the product that is *derived* rather than stored: `invoice_line_id`
 * and `billable` together are what the three readings mean.
 */
describe("an hour's state", () => {
  it('reads an hour with an invoice line as invoiced, whatever fatturabile says', () => {
    expect(statusOf({ ...ENTRY, invoice_line_id: 'l1', billable: false })).toBe('invoiced')
  })

  it('reads a billable hour with no line as still to be invoiced', () => {
    expect(statusOf({ ...ENTRY, billable: true })).toBe('to_invoice')
  })

  it('reads a non-billable hour as such, not as pending', () => {
    expect(statusOf({ ...ENTRY, billable: false })).toBe('non_billable')
  })

  it('shows an hour still to invoice in the waiting-on-somebody tone', () => {
    renderCell('status', ENTRY)
    expect(screen.getByText('Da fatturare').closest('[data-slot="badge"]')).toHaveAttribute(
      'data-tone',
      'gold',
    )
  })

  it('shows an invoiced hour as settled', () => {
    renderCell('status', { ...ENTRY, invoice_line_id: 'l1' })
    expect(screen.getByText('Fatturata').closest('[data-slot="badge"]')).toHaveAttribute(
      'data-tone',
      'ink',
    )
  })

  /** Most non-billable hours are deliberate (internal work), so the column must not
   *  read as a table of problems. */
  it('keeps a non-billable hour quiet rather than tinting it as a warning', () => {
    renderCell('status', { ...ENTRY, billable: false })
    expect(screen.getByText('Non fatturabile').closest('[data-slot="badge"]')).toHaveAttribute(
      'data-tone',
      'muted',
    )
    expect(TIME_ENTRY_STATE_TONE.non_billable).not.toBe('danger')
  })
})
