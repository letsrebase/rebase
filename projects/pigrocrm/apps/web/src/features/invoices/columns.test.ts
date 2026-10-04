import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { buildInvoiceColumns, type InvoiceColumnOptions } from './columns'
import type { Invoice } from './queries'

const ISSUED = {
  anno: 2026,
  numero: 7,
  riferimento: null,
  tipo: 'fattura',
  stato: 'emessa',
  stato_pagamento: 'da_incassare',
  data_emissione: '2026-08-20',
  totale: '1500.00',
} as unknown as Invoice

const PROFORMA = {
  anno: null,
  numero: null,
  riferimento: 'PROV-2026-0007',
  tipo: 'proforma',
  stato: 'confermata',
  stato_pagamento: 'da_incassare',
  data_emissione: null,
  totale: '500.00',
} as unknown as Invoice

const WITH_CUSTOMER = { ...ISSUED, customer_ragione_sociale: 'ACME S.r.l.' } as Invoice

const LONG_CAUSALE =
  'Consulenza tecnica e sviluppo software per il progetto di migrazione della piattaforma, ' +
  'comprensiva di analisi, implementazione, test e supporto al rilascio in produzione'

const WITH_CAUSALE = { ...ISSUED, causale: 'Consulenza agosto' } as Invoice

const WITH_PERIOD = {
  ...ISSUED,
  competenza_da: '2026-08-01',
  competenza_a: '2026-08-31',
} as Invoice

function accessor(id: string, row: Invoice, options?: InvoiceColumnOptions): unknown {
  const column = buildInvoiceColumns(options).find((candidate) => candidate.id === id)
  if (column === undefined || !('accessorFn' in column) || column.accessorFn === undefined) {
    throw new Error(`nessuna colonna con id ${id}`)
  }
  return column.accessorFn(row, 0)
}

describe('buildInvoiceColumns', () => {
  it('shows the fiscal number for an invoice and the reference for a proforma', () => {
    expect(accessor('numero', ISSUED)).toBe('2026/7')
    expect(accessor('numero', PROFORMA)).toBe('PROV-2026-0007')
  })

  it('formats the total as grouped euros', () => {
    expect(String(accessor('totale', ISSUED))).toContain('1.500,00')
  })

  it('shows an em dash where a proforma has no issue date', () => {
    expect(accessor('data_emissione', PROFORMA)).toBe('—')
  })

  it('does not offer a payment column value for a proforma', () => {
    // A proforma is never collected: showing "Da incassare" next to something nobody
    // owes is the kind of small lie a fiscal list should not tell.
    expect(accessor('stato_pagamento', PROFORMA)).toBe('—')
    expect(accessor('stato_pagamento', ISSUED)).toBe('Da incassare')
  })
})

/**
 * Reads a column's *rendered* cell, not only its accessor.
 *
 * Both halves of every column below still matter: the accessor keeps the plain text
 * value (asserted above, and what a future sort or export would read), while `cell`
 * carries the presentation the design revision asks for -- a calendar icon before a
 * date, a right-aligned figure, a state as a dotted pill (design spec §4). Only
 * `row.original` is consulted by these cells, so the cast supplies exactly that.
 */
function renderCell(id: string, row: Invoice, options?: InvoiceColumnOptions) {
  const column = buildInvoiceColumns(options).find((candidate) => candidate.id === id)
  if (column === undefined || typeof column.cell !== 'function') {
    throw new Error(`la colonna ${id} non ha un cell renderer`)
  }
  return render(column.cell({ row: { original: row } } as never) as ReactElement)
}

function column(id: string, options?: InvoiceColumnOptions) {
  const found = buildInvoiceColumns(options).find((candidate) => candidate.id === id)
  if (found === undefined) throw new Error(`nessuna colonna con id ${id}`)
  return found
}

describe('how an invoice row reads', () => {
  it('puts a calendar icon before the issue date', () => {
    const { container } = renderCell('data_emissione', ISSUED)
    expect(screen.getByText('20/08/2026')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('shows no date and no calendar icon for a proforma that has not been issued', () => {
    const { container } = renderCell('data_emissione', PROFORMA)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(container.querySelector('svg')).toBeNull()
  })

  /** The header has to sit over the digits it labels, which only the column can say --
   *  a cell that right-aligned itself would leave its own header on the left. */
  it('declares the total a right-aligned column, not merely a right-aligned cell', () => {
    expect(column('totale').meta).toEqual({ align: 'right' })
  })

  it('right-aligns the total inside its cell too', () => {
    renderCell('totale', ISSUED)
    expect(screen.getByText(/1\.500,00/).className).toContain('text-right')
  })

  it('shows the fiscal state as a pill, tinted by what the state means', () => {
    renderCell('stato', ISSUED)
    const pill = screen.getByText('Emessa').closest('[data-slot="badge"]')
    expect(pill).toHaveAttribute('data-tone', 'ink')
  })

  it('shows an annulled invoice as a warning, because it keeps its number and stays in the register', () => {
    renderCell('stato', { ...ISSUED, stato: 'annullata' })
    expect(screen.getByText('Annullata').closest('[data-slot="badge"]')).toHaveAttribute(
      'data-tone',
      'danger',
    )
  })

  it('shows money still owed in the waiting-on-somebody tone', () => {
    renderCell('stato_pagamento', ISSUED)
    expect(screen.getByText('Da incassare').closest('[data-slot="badge"]')).toHaveAttribute(
      'data-tone',
      'gold',
    )
  })

  it('shows a collected invoice as settled, not as waiting', () => {
    renderCell('stato_pagamento', { ...ISSUED, stato_pagamento: 'incassato' })
    expect(screen.getByText('Incassato').closest('[data-slot="badge"]')).toHaveAttribute(
      'data-tone',
      'ink',
    )
  })

  /** A pill is a claim about a state, and a proforma has no collection state to claim:
   *  it reads as the plain dash the accessor already returns, never as a pill. */
  it('shows the payment column of a proforma as a plain dash, with no pill at all', () => {
    const { container } = renderCell('stato_pagamento', PROFORMA)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="badge"]')).toBeNull()
  })
})

/**
 * The «Cliente» column (ORB-98). Opt-in, because the same columns draw the Fatture tab
 * inside a customer's page, where every row belongs to the customer named in the title:
 * a column repeating it on each line would be noise there and information only on the
 * list page, which is the one screen that mixes customers.
 */
describe('the customer column', () => {
  const CLIENTE = { cliente: true }

  function ids(options?: InvoiceColumnOptions): string[] {
    return buildInvoiceColumns(options).map((candidate) => candidate.id ?? '')
  }

  it('is not part of the default columns', () => {
    expect(ids()).not.toContain('cliente')
  })

  it('sits right after the number when asked for', () => {
    const columns = ids(CLIENTE)
    expect(columns.indexOf('cliente')).toBe(columns.indexOf('numero') + 1)
  })

  it('reads the ragione sociale the API resolved for the row', () => {
    expect(column('cliente', CLIENTE).header).toBe('Cliente')
    expect(accessor('cliente', WITH_CUSTOMER, CLIENTE)).toBe('ACME S.r.l.')
  })

  /** `customer_ragione_sociale` is `str | None` on the server: the association always
   *  exists, the name may fail to resolve. That reads as the same dash every other
   *  empty cell shows, never as an empty string a reader could mistake for a customer
   *  with no name -- and `''`, which `ragione_sociale` can legitimately hold, reads the
   *  same way. */
  it('shows the em dash when the name did not resolve or is empty', () => {
    const unresolved = { ...ISSUED, customer_ragione_sociale: null } as Invoice
    const blank = { ...ISSUED, customer_ragione_sociale: '' } as Invoice
    expect(accessor('cliente', unresolved, CLIENTE)).toBe('—')
    expect(accessor('cliente', blank, CLIENTE)).toBe('—')
    expect(accessor('cliente', ISSUED, CLIENTE)).toBe('—')
  })

  it('renders the name as plain text and the missing name as a quiet dash', () => {
    renderCell('cliente', WITH_CUSTOMER, CLIENTE)
    expect(screen.getByText('ACME S.r.l.')).toBeInTheDocument()

    renderCell('cliente', ISSUED, CLIENTE)
    expect(screen.getByText('—').className).toContain('text-muted-foreground')
  })
})

/**
 * The «Competenza» column (ORB-126). Invoicing runs late here, August's work issued in
 * September, so the emission date alone does not say which month a row is about. In the
 * list and in the tab alike: unlike the customer's name, the period is not what the
 * page title already says.
 */
describe('the accrual period column', () => {
  it('sits right after the date, with and without the customer column', () => {
    for (const options of [undefined, { cliente: true }]) {
      const ids = buildInvoiceColumns(options).map((candidate) => candidate.id ?? '')
      expect(ids.indexOf('competenza')).toBe(ids.indexOf('data_emissione') + 1)
    }
    expect(column('competenza').header).toBe('Competenza')
  })

  it('reads the period the way the detail page states it', () => {
    expect(accessor('competenza', WITH_PERIOD)).toBe('01/08/2026 - 31/08/2026')
  })

  it('shows the em dash when the document declares no period', () => {
    expect(accessor('competenza', ISSUED)).toBe('—')
  })

  /** The emission date is the one column with a calendar: a second icon two columns
   *  over would make the two read as the same kind of value, and they are not. */
  it('renders as plain text, with no calendar icon, and the dash quietly', () => {
    const { container } = renderCell('competenza', WITH_PERIOD)
    expect(screen.getByText('01/08/2026 - 31/08/2026')).toBeInTheDocument()
    expect(container.querySelector('svg')).toBeNull()

    renderCell('competenza', ISSUED)
    expect(screen.getByText('—').className).toContain('text-muted-foreground')
  })
})

/**
 * The «Descrizione» column (ORB-130): the causale, which until now the list never
 * showed. A causale can be 200 characters long, so the cell is the one place in this
 * table that truncates: the column takes what the others leave, down to a floor, and the
 * text clips there with the whole causale as the cell's title, rather than a column that
 * widens the table to fit the longest row.
 */
describe('the description column', () => {
  it('sits right after the customer when the list shows one, and after the number otherwise', () => {
    const withCustomer = buildInvoiceColumns({ cliente: true }).map((c) => c.id ?? '')
    expect(withCustomer.indexOf('descrizione')).toBe(withCustomer.indexOf('cliente') + 1)
    const plain = buildInvoiceColumns().map((c) => c.id ?? '')
    expect(plain.indexOf('descrizione')).toBe(plain.indexOf('numero') + 1)
    expect(column('descrizione').header).toBe('Descrizione')
  })

  it('reads the causale, or the em dash when there is none', () => {
    expect(accessor('descrizione', WITH_CAUSALE)).toBe('Consulenza agosto')
    expect(accessor('descrizione', { ...ISSUED, causale: null } as Invoice)).toBe('—')
    expect(accessor('descrizione', { ...ISSUED, causale: '' } as Invoice)).toBe('—')
  })

  /** The column takes what the others leave (`width: 100%` on the header) and the span
   *  fills exactly that (a block of width zero whose minimum is the whole cell, never
   *  less than 12rem), so the text truncates at the column's width instead of widening
   *  the table, and the column cannot shrink to its header on a narrow screen. */
  it('truncates a long causale at the width the column gets and keeps the whole text as the title', () => {
    expect(column('descrizione').meta).toEqual({ width: '100%' })
    renderCell('descrizione', { ...ISSUED, causale: LONG_CAUSALE } as Invoice)
    const cell = screen.getByTitle(LONG_CAUSALE)
    expect(cell).toHaveTextContent(LONG_CAUSALE)
    for (const cls of ['block', 'w-0', 'min-w-[max(100%,12rem)]', 'truncate']) {
      expect(cell.className).toContain(cls)
    }
  })

  it('shows the missing causale as a quiet dash', () => {
    renderCell('descrizione', { ...ISSUED, causale: null } as Invoice)
    expect(screen.getByText('—').className).toContain('text-muted-foreground')
  })
})

/**
 * The state column no longer stamps «importata» on a row (ORB-130): in a register where
 * most rows came from the previous system the word explains nothing a reader of the list
 * can act on. The detail page keeps it, where it says why the XML and «Rigenera
 * documenti» are missing.
 */
describe('the state column and an imported invoice', () => {
  it('shows the state and not the provenance', () => {
    renderCell('stato', { ...ISSUED, importata_da: 'esterno' } as Invoice)
    expect(screen.getByText('Emessa')).toBeInTheDocument()
    expect(screen.queryByText(/^importata$/i)).toBeNull()
  })
})

describe('the azienda beside the number (REB-625)', () => {
  const AZIENDE = [
    { id: 'a1', nome: 'humancraft' },
    { id: 'a2', nome: 'rebase' },
  ] as InvoiceColumnOptions['aziende']

  function renderNumber(row: Invoice, options?: InvoiceColumnOptions) {
    const column = buildInvoiceColumns(options).find((candidate) => candidate.id === 'numero')
    if (column === undefined || typeof column.cell !== 'function') {
      throw new Error('the «Numero» column has no cell renderer')
    }
    return render(column.cell({ row: { original: row } } as never) as ReactElement)
  }

  it('names the azienda under «tutte» and keeps the bare number as the sortable value', () => {
    const row = { ...ISSUED, azienda_id: 'a2' } as Invoice
    const { container } = renderNumber(row, { aziende: AZIENDE })
    expect(container).toHaveTextContent('2026/7 · rebase')
    expect(accessor('numero', row, { aziende: AZIENDE })).toBe('2026/7')
  })

  it('draws the bare number with one azienda, with one selected, and for an azienda no longer active', () => {
    const row = { ...ISSUED, azienda_id: 'a2' } as Invoice
    expect(renderNumber(row).container).toHaveTextContent(/^2026\/7$/)
    expect(renderNumber({ ...row, azienda_id: 'gone' } as Invoice, { aziende: AZIENDE }).container)
      .toHaveTextContent(/^2026\/7$/)
  })
})
