import { describe, expect, it } from 'vitest'
import {
  formatDate,
  formatInvoiceNumber,
  formatMoney,
  formatPeriod,
  formatPurchaseOrder,
  formatQuantity,
  formatRate,
  previewImponibile,
  sumLineTotals,
} from './format'

describe('formatMoney', () => {
  it('always shows the thousands separator', () => {
    // `useGrouping: 'always'` is not optional: the default withholds the separator
    // below five integer digits, so 1.500,00 EUR would print as 1500,00 EUR.
    expect(formatMoney('1500.00')).toContain('1.500,00')
  })

  it('renders a null as an em dash, not as zero', () => {
    expect(formatMoney(null)).toBe('—')
  })

  it('keeps a negative line readable', () => {
    expect(formatMoney('-200.00')).toContain('200,00')
  })
})

describe('sumLineTotals', () => {
  it('adds integer cents, never JS floats', () => {
    // Number('0.29') * 100 is 28.999999999999996. On an invoice that is a wrong total.
    expect(sumLineTotals([{ prezzo_totale: '0.29' }, { prezzo_totale: '0.01' }])).toBe('0.30')
  })

  it('handles a negative discount line', () => {
    expect(sumLineTotals([{ prezzo_totale: '1000.00' }, { prezzo_totale: '-200.00' }])).toBe(
      '800.00',
    )
  })

  it('is zero for no lines', () => {
    expect(sumLineTotals([])).toBe('0.00')
  })
})

describe('formatQuantity and formatRate', () => {
  it('shows a quantity with its six decimals trimmed to what matters', () => {
    expect(formatQuantity('3.000000')).toBe('3')
    expect(formatQuantity('3.500000')).toBe('3,5')
  })

  it('shows a rate as a percentage', () => {
    expect(formatRate('0.00')).toBe('0%')
    expect(formatRate('22.00')).toBe('22%')
  })
})

describe('formatInvoiceNumber', () => {
  it('is year slash number for an issued invoice', () => {
    expect(formatInvoiceNumber({ anno: 2026, numero: 7, riferimento: null })).toBe('2026/7')
  })

  it('is the reference for a proforma', () => {
    expect(formatInvoiceNumber({ anno: null, numero: null, riferimento: 'PROV-2026-0007' })).toBe(
      'PROV-2026-0007',
    )
  })

  it('is an em dash for a draft with neither', () => {
    expect(formatInvoiceNumber({ anno: null, numero: null, riferimento: null })).toBe('—')
  })
})

describe('formatDate', () => {
  it('renders an ISO date in Italian without touching the timezone', () => {
    // Parsed field by field, never `new Date('2026-01-01')`, which is UTC midnight and
    // renders as 31 December west of Greenwich -- the same class of defect as
    // `toISOString()` on the backend. The property under test is that the day stays
    // 1 January, not 31 December -- the exact zero-padding is CLDR data's call, not
    // this function's: current ICU renders `it-IT` dates zero-padded ("01/01/2026"),
    // where an older CLDR snapshot produced "1/1/2026".
    expect(formatDate('2026-01-01')).toBe('01/01/2026')
  })

  it('renders a null as an em dash', () => {
    expect(formatDate(null)).toBe('—')
  })
})

describe('previewImponibile', () => {
  it('sums quantity times price for every row', () => {
    expect(
      previewImponibile([
        { quantita: '2', prezzo_unitario: '150.00' },
        { quantita: '1', prezzo_unitario: '99.90' },
      ]),
    ).toBe('399.90')
  })

  it('adds cents as integers, never as floats', () => {
    // `0.29 * 100` is 28.999999999999996 in this project's own Node runtime, and three
    // rows of it is how a preview total ends in ...86 instead of ...87. Every figure
    // here goes through `scaledFromDecimalString` (lib/decimal.ts).
    expect(
      previewImponibile([
        { quantita: '1', prezzo_unitario: '0.29' },
        { quantita: '1', prezzo_unitario: '0.29' },
        { quantita: '1', prezzo_unitario: '0.29' },
      ]),
    ).toBe('0.87')
  })

  it('handles a fractional quantity, which is what hours are', () => {
    expect(previewImponibile([{ quantita: '7.5', prezzo_unitario: '80.00' }])).toBe('600.00')
  })

  it('treats an empty or unpriced row as nothing, not as a failure', () => {
    // A row being typed is the normal state of this form: the total simply does not
    // count it yet, rather than reading NaN while the user is mid-keystroke.
    expect(previewImponibile([{ quantita: '', prezzo_unitario: '' }])).toBe('0.00')
    expect(
      previewImponibile([
        { quantita: '2', prezzo_unitario: '10.00' },
        { quantita: '1', prezzo_unitario: '' },
      ]),
    ).toBe('20.00')
  })

  it('rounds a fractional cent to the nearest one', () => {
    // 0.5 x 0.05 = 0.025. Half-up, and the only reason a preview may differ from the
    // stored figure by a cent -- the server computes the real one at full scale.
    expect(previewImponibile([{ quantita: '0.5', prezzo_unitario: '0.05' }])).toBe('0.03')
  })
})

describe('formatDate on an empty value', () => {
  /** The accessor and `DateCell` render the same column: they have to agree on what
   *  «nothing» looks like, and the em dash is the answer the rest of the product gives. */
  it('reads an explicitly-cleared ("") date as absent, exactly as null is', () => {
    expect(formatDate('')).toBe('—')
    expect(formatDate(null)).toBe('—')
  })
})

describe('formatPeriod', () => {
  it('joins the two ends the way the emission date beside them is written', () => {
    expect(formatPeriod('2026-08-01', '2026-08-31')).toBe(
      `${formatDate('2026-08-01')} - ${formatDate('2026-08-31')}`,
    )
  })

  it('renders no period as an em dash', () => {
    expect(formatPeriod(null, null)).toBe('—')
  })

  it('renders a lone end as an em dash too, since the server never stores one', () => {
    expect(formatPeriod('2026-08-01', null)).toBe('—')
    expect(formatPeriod('', '2026-08-31')).toBe('—')
  })

  it('survives an API that predates the two columns', () => {
    // `undefined`, not `null`: the key is simply absent from an older `InvoiceRead`.
    expect(formatPeriod(undefined as unknown as null, undefined as unknown as null)).toBe('—')
  })
})

describe('formatPurchaseOrder', () => {
  it('reads like the PDF line, with only the parts the order has', () => {
    expect(
      formatPurchaseOrder({
        ordine_numero: 'ODA-14',
        ordine_data: '2026-10-09',
        ordine_cig: 'Z0A1B2C3D4',
        ordine_cup: 'J41E22000110005',
      }),
    ).toBe(`n. ODA-14 del ${formatDate('2026-10-09')}, CIG Z0A1B2C3D4, CUP J41E22000110005`)
    expect(
      formatPurchaseOrder({ ordine_numero: '8', ordine_data: null, ordine_cig: null, ordine_cup: null }),
    ).toBe('n. 8')
  })

  it('is the dash without an order number', () => {
    expect(
      formatPurchaseOrder({ ordine_numero: null, ordine_data: null, ordine_cig: null, ordine_cup: null }),
    ).toBe('—')
  })
})
