import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { buildCostColumns } from './columns'
import type { Cost, CostCategory } from './queries'

/* Written out in full rather than cast: a double cast switches the type check off, so
   the day `CostCategory` grows a required field this fixture would keep compiling and
   say nothing. The column reads only `id` and `nome`; the rest costs four lines. */
const CATEGORIES: CostCategory[] = [
  {
    id: 'cat-1',
    nome: 'Consulenze',
    posizione: 0,
    code: null,
    archiviata: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

const COST: Cost = {
  id: 'k1',
  azienda_id: null,
  deal_id: null,
  category_id: 'cat-1',
  data: '2026-03-05',
  importo: '2500.50',
  descrizione: 'Consulenza fiscale',
  fornitore: 'Studio Rossi',
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
function renderCell(id: string, cost: Cost) {
  const column = buildCostColumns(CATEGORIES, []).find((candidate) => candidate.id === id)
  if (column === undefined || typeof column.cell !== 'function') {
    throw new Error(`la colonna ${id} non ha un cell renderer`)
  }
  return render(column.cell({ row: { original: cost } } as never) as ReactElement)
}

describe('how a cost row reads', () => {
  it('puts a calendar icon before the date, in Italian day/month/year order', () => {
    const { container } = renderCell('data', COST)
    expect(screen.getByText('05/03/2026')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  /** The header has to sit over the digits it labels, which only the column can say --
   *  a cell that right-aligned itself would leave its own header on the left. */
  it('declares the amount a right-aligned column', () => {
    const column = buildCostColumns(CATEGORIES, []).find((candidate) => candidate.id === 'importo')
    expect(column?.meta).toEqual({ align: 'right' })
  })

  it('right-aligns the amount inside its cell too', () => {
    renderCell('importo', COST)
    expect(screen.getByText(/2\.500,50/).className).toContain('text-right')
  })

  /** §4.4: a negative amount is a refund or a credit note received, so it stays
   *  labelled rather than being treated as an error -- and the label survives the move
   *  into a right-aligned cell. */
  it('keeps saying «(rimborso)» beside a negative amount', () => {
    renderCell('importo', { ...COST, importo: '-45.50' })
    expect(screen.getByText(/\(rimborso\)/)).toBeInTheDocument()
  })
})
