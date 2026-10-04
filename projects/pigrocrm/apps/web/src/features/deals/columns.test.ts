import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { buildDealColumns, displayNative, formatDate, formatHours, formatMoney, sumValorePrevisto } from './columns'
import type { FieldDefinition } from '@/lib/schema'
import { DEAL_STAGE_TONE, type Deal } from './queries'

// `Intl.NumberFormat('it-IT', {style:'currency',...})` separates the amount from
// "€" with U+00A0 (NO-BREAK SPACE), not an ordinary U+0020 -- checked directly
// against this stack's ICU. An exact `toBe` against a string typed with a plain
// space would never match, silently, for a reason with nothing to do with the
// formatting logic under test.
const NBSP = '\u00a0'

/**
 * Reads a native column's cell value the same way `DataTable` does internally --
 * through the column's own `accessorFn` -- mirroring `features/customers/
 * queries.test.ts`/`features/people/columns.test.ts`'s identical helper.
 */
function cellValue(column: ReturnType<typeof buildDealColumns>[number], deal: Deal) {
  if (!('accessorFn' in column) || typeof column.accessorFn !== 'function') {
    throw new Error(`column "${String(column.header)}" has no accessorFn to read`)
  }
  return column.accessorFn(deal, 0)
}

const BASE_DEAL: Deal = {
  id: 'd1',
  azienda_id: 'a-1',
  nome: 'Sito vetrina',
  customer_id: 'c1',
  pipeline_stage_id: 's1',
  valore_previsto: null,
  probabilita: 10,
  data_chiusura_prevista: null,
  owner_id: null,
  note: null,
  ore_preventivate: null,
  valore_preventivato: null,
  // Slice 4A added the per-deal hourly rate to `DealRead`; a deal that has never had
  // one set reads `null` and falls back to the user's own default (§7.2).
  tariffa_oraria: null,
  // Slice 6 §4.1 added the closing day to `DealRead`. `null` on an open deal, and
  // deliberately not backfilled on deals closed before the column existed -- the
  // commercial dashboard declares how many of those there are rather than guessing.
  chiuso_il: null,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

describe('buildDealColumns', () => {
  it('always shows the core commercial columns', () => {
    const headers = buildDealColumns([]).map((column) => column.header)
    expect(headers).toEqual(['Nome', 'Valore previsto', 'Probabilità', 'Chiusura prevista'])
  })

  it('appends one column per custom field', () => {
    const fields: FieldDefinition[] = [
      { key: 'fonte', label: 'Fonte', type: 'text', required: false, options: [] },
    ]
    const headers = buildDealColumns(fields).map((column) => column.header)
    expect(headers).toContain('Fonte')
  })

  it('does not add columns for archived fields, which are simply not returned', () => {
    expect(buildDealColumns([])).toHaveLength(4)
  })

  it('renders a null valore_previsto as the empty dash, not zero', () => {
    const [, valore] = buildDealColumns([])
    expect(cellValue(valore!, { ...BASE_DEAL, valore_previsto: null })).toBe('—')
  })

  it('renders a present valore_previsto with Italian currency formatting', () => {
    const [, valore] = buildDealColumns([])
    expect(cellValue(valore!, { ...BASE_DEAL, valore_previsto: '2500.50' })).toBe(`2.500,50${NBSP}€`)
  })

  it('renders probabilita as a percentage, including a real zero', () => {
    const [, , probabilita] = buildDealColumns([])
    expect(cellValue(probabilita!, { ...BASE_DEAL, probabilita: 0 })).toBe('0%')
    expect(cellValue(probabilita!, { ...BASE_DEAL, probabilita: 50 })).toBe('50%')
  })

  it('renders a null chiusura prevista as the empty dash', () => {
    const [, , , chiusura] = buildDealColumns([])
    expect(cellValue(chiusura!, { ...BASE_DEAL, data_chiusura_prevista: null })).toBe('—')
  })

  it('renders a present chiusura prevista in Italian date order', () => {
    const [, , , chiusura] = buildDealColumns([])
    expect(cellValue(chiusura!, { ...BASE_DEAL, data_chiusura_prevista: '2026-08-06' })).toBe(
      '06/08/2026',
    )
  })

  /** The table is one of the two read surfaces the "absent checkbox reads No"
   *  rule has to hold on (the detail page is the other, and goes through the
   *  same `renderFieldValue`). */
  it('renders a checkbox custom column as No when the record carries no value for it', () => {
    const attivo: FieldDefinition = {
      key: 'urgente',
      label: 'Urgente',
      type: 'checkbox',
      required: false,
      options: [],
    }
    const [, , , , urgente] = buildDealColumns([attivo])
    expect(cellValue(urgente!, { ...BASE_DEAL, custom_fields: {} })).toBe('No')
    expect(cellValue(urgente!, { ...BASE_DEAL, custom_fields: { urgente: true } })).toBe('Sì')
  })

  it('keeps zero and false as real custom-field values, not a dash', () => {
    const punteggio: FieldDefinition = {
      key: 'punteggio',
      label: 'Punteggio',
      type: 'number',
      required: false,
      options: [],
    }
    const [, , , , score] = buildDealColumns([punteggio])
    expect(cellValue(score!, { ...BASE_DEAL, custom_fields: { punteggio: 0 } })).toBe('0')
  })
})

describe('displayNative', () => {
  it('treats null and empty string as the same absent value', () => {
    expect(displayNative(null)).toBe('—')
    expect(displayNative('')).toBe('—')
  })

  it('renders a present value as itself', () => {
    expect(displayNative('Richiamare a settembre')).toBe('Richiamare a settembre')
  })
})

describe('formatMoney', () => {
  it('renders null as the empty dash', () => {
    expect(formatMoney(null)).toBe('—')
  })

  it('formats with the thousands separator it-IT readers expect starting at four digits', () => {
    expect(formatMoney('1234.56')).toBe(`1.234,56${NBSP}€`)
  })
})

describe('formatHours', () => {
  it('renders null as the empty dash', () => {
    expect(formatHours(null)).toBe('—')
  })

  it('formats a quantity with Italian grouping, no currency symbol', () => {
    expect(formatHours('1234.50')).toBe('1.234,5')
  })
})

describe('formatDate', () => {
  it('renders null as the empty dash', () => {
    expect(formatDate(null)).toBe('—')
  })

  /** Guards the same timezone trap `DynamicFieldRenderer.tsx`'s own
   *  `formatIsoDateItalian` documents: parsing the ISO string with the
   *  built-in `Date` constructor reads it as UTC midnight, which formats a day
   *  early in any zone behind UTC. This test's own runtime zone is whatever
   *  the CI/dev machine is in, so it does not by itself prove the fix on every
   *  zone -- it only proves the *chosen* implementation matches the one
   *  already verified for exactly this failure mode elsewhere. */
  it('renders a present ISO date in Italian day/month/year order', () => {
    expect(formatDate('2026-01-05')).toBe('05/01/2026')
  })
})

describe('sumValorePrevisto', () => {
  it('renders zero for an empty column', () => {
    expect(sumValorePrevisto([])).toBe(`0,00${NBSP}€`)
  })

  it('ignores unpriced (null) deals rather than treating them as zero-value contributions', () => {
    const deals = [
      { ...BASE_DEAL, id: 'a', valore_previsto: '10.00' },
      { ...BASE_DEAL, id: 'b', valore_previsto: null },
    ]
    expect(sumValorePrevisto(deals)).toBe(`10,00${NBSP}€`)
  })

  it('sums several deals exactly, with the thousands separator', () => {
    const deals = [
      { ...BASE_DEAL, id: 'a', valore_previsto: '1000.00' },
      { ...BASE_DEAL, id: 'b', valore_previsto: '2500.50' },
    ]
    expect(sumValorePrevisto(deals)).toBe(`3.500,50${NBSP}€`)
  })

  /**
   * The regression this whole function exists to prevent: `deals.reduce((sum,
   * deal) => sum + Number(deal.valore_previsto ?? 0), 0)` (the brief's own line
   * 261) sums money as binary floats, and `Numeric(12, 2)` exists on the
   * backend precisely because a binary float cannot hold money exactly.
   *
   * A *small*, everyday-looking set of deal amounts essentially never shows a
   * visibly wrong two-decimal total under that naive approach -- the per-term
   * error is many orders of magnitude below the rounding threshold (checked
   * directly: 200,000 random 3-8 deal combinations under six figures each
   * never once produced a different formatted total). It takes either a very
   * large number of deals or amounts priced near the top of `Numeric(12, 2)`'s
   * own range -- both realistic for a busy agency's total pipeline value -- to
   * push the naive total's *displayed* cents off by one. This is exactly such
   * a case: 300 deals, each priced just under the column's ceiling, summed in
   * exact cents give `2955149865447.00` -- independently cross-checked with
   * Python's arbitrary-precision `Decimal` on the identical formula -- while
   * summing the same 300 values as plain JS floats (`Number(v)` added
   * directly, no multiplication) displays as `...447,02 €`, two cents high,
   * which `Intl.NumberFormat` renders with no hint anything went wrong.
   */
  it('stays exact summing 300 deals priced near the Numeric(12,2) ceiling, where a naive float sum visibly drifts', () => {
    const deals = Array.from({ length: 300 }, (_, i) => ({
      ...BASE_DEAL,
      id: `deal-${i}`,
      valore_previsto: String(9999999999 - i * 1000003) + '.99',
    }))

    // The regression itself, computed the brief's own flawed way, right here in
    // the test -- so this assertion documents the failure it guards against,
    // not just the fix.
    const naiveTotal = deals.reduce((sum, deal) => sum + Number(deal.valore_previsto ?? 0), 0)
    const naiveFormatted = new Intl.NumberFormat('it-IT', {
      style: 'currency',
      currency: 'EUR',
      useGrouping: 'always',
    }).format(naiveTotal)
    expect(naiveFormatted).toBe(`2.955.149.865.447,02${NBSP}€`)

    expect(sumValorePrevisto(deals)).toBe(`2.955.149.865.447,00${NBSP}€`)
  })
})

/**
 * Reads a column's *rendered* cell rather than only its accessor: the accessor keeps
 * the plain text value asserted above, while `cell` carries what the design revision
 * adds -- an initials chip on the deal's name, a right-aligned figure, a calendar icon
 * before the date (design spec §4). Only `row.original` is consulted, so the cast
 * supplies exactly that and nothing else.
 */
function renderCell(index: number, deal: Deal) {
  const column = buildDealColumns([])[index]
  if (column === undefined || typeof column.cell !== 'function') {
    throw new Error(`la colonna ${index} non ha un cell renderer`)
  }
  return render(column.cell({ row: { original: deal } } as never) as ReactElement)
}

describe('how a deal row reads', () => {
  it('carries the deal name beside a chip of its initials', () => {
    renderCell(0, { ...BASE_DEAL, nome: 'Sito vetrina' })
    expect(screen.getByText('Sito vetrina')).toBeInTheDocument()
    expect(screen.getByText('SV')).toBeInTheDocument()
  })

  /**
   * The line under the deal's name is who the work is for -- what turns a list of
   * project names into a list of work (design spec §4). It has to be asserted on the
   * *rendered* cell: the column's `accessorFn` is still the bare `nome`, so a
   * misspelled `customer_ragione_sociale` here would leave every `cellValue` assertion
   * in this file green.
   */
  it('names the deal\u2019s customer on the line under it', () => {
    renderCell(0, { ...BASE_DEAL, customer_ragione_sociale: 'ACME Srl' })
    expect(screen.getByText('ACME Srl')).toBeInTheDocument()
  })

  /** `DealRead` fills the name from a batched lookup, so a row that arrived before that
   *  field existed (or from an API that does not send it yet) carries `undefined`. One
   *  line, not an empty second line under the name: `EntityCell` treats
   *  `undefined`/`null`/`''` alike, and this is the assertion that keeps it that way. */
  it('draws no second line for a deal whose customer name did not come back', () => {
    renderCell(0, { ...BASE_DEAL, customer_ragione_sociale: null })
    // The name's own wrapper holds the name and, when there is one, the sub-line. One
    // child means no second line at all -- not a second line rendered empty, which
    // would take the vertical space and make every row of the table taller for nothing.
    expect(screen.getByText('Sito vetrina').parentElement?.children).toHaveLength(1)
  })

  /** The header has to sit over the digits it labels, which only the column can say. */
  it('declares value and probability right-aligned columns, so their headers move too', () => {
    const [, valore, probabilita] = buildDealColumns([])
    expect(valore?.meta).toEqual({ align: 'right' })
    expect(probabilita?.meta).toEqual({ align: 'right' })
  })

  it('right-aligns the expected value inside its own cell', () => {
    renderCell(1, { ...BASE_DEAL, valore_previsto: '2500.50' })
    expect(screen.getByText(/2\.500,50/).className).toContain('text-right')
  })

  /** A percentage is a figure: it needs the tabular digits that make a column of them
   *  line up, which `meta.align` alone cannot give (that moves the header). Asserted on
   *  the rendered cell because `NumberCell` is a `cell` renderer and the `accessorFn`
   *  assertion above it goes nowhere near one. */
  it('renders the probability through NumberCell, with tabular figures', () => {
    renderCell(2, { ...BASE_DEAL, probabilita: 10 })
    const cell = screen.getByText('10%')
    expect(cell.className).toContain('tabular-nums')
    expect(cell.className).toContain('text-right')
  })

  it('puts a calendar icon before the expected closing date', () => {
    const { container } = renderCell(3, { ...BASE_DEAL, data_chiusura_prevista: '2026-08-06' })
    expect(screen.getByText('06/08/2026')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('shows an unpriced closing date as the dash, with no calendar icon claiming a date', () => {
    const { container } = renderCell(3, { ...BASE_DEAL, data_chiusura_prevista: null })
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(container.querySelector('svg')).toBeNull()
  })
})

/**
 * Keyed on `tipo`, never on the stage's name: a tenant renames its stages freely, and
 * only `open`/`won`/`lost` is guaranteed by the backend.
 */
describe('DEAL_STAGE_TONE', () => {
  it('states only the two ends of the pipeline and keeps every open stage quiet', () => {
    expect(DEAL_STAGE_TONE).toEqual({ open: 'muted', won: 'ink', lost: 'danger' })
  })
})
