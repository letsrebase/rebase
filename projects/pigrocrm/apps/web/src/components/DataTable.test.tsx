import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DataTable, type DataTableFeatures } from './DataTable'
import type { ColumnDef } from '@tanstack/react-table'

interface Riga {
  id: string
  nome: string
}

// Two columns on purpose: a plain `accessorKey` (the default "just call
// `getValue()`" cell) and an explicit `cell` render function, so the tests below
// exercise both branches of `table.FlexRender` (v9's replacement for calling
// `flexRender` by hand -- see DataTable.tsx's own comment on why v9's `useTable`
// is used at all here), not only the simpler of the two.
const COLUMNS: ColumnDef<DataTableFeatures, Riga>[] = [
  { accessorKey: 'id', header: 'ID', cell: (info) => `#${info.getValue()}` },
  { accessorKey: 'nome', header: 'Nome' },
]

const ACME: Riga = { id: '1', nome: 'ACME Srl' }
const BETA: Riga = { id: '2', nome: 'Beta SpA' }
const DATA: Riga[] = [ACME, BETA]

function rowFor(text: string): HTMLElement {
  const cell = screen.getByText(text)
  const row = cell.closest('tr')
  if (!row) throw new Error(`no <tr> ancestor for "${text}"`)
  return row
}

describe('DataTable', () => {
  it('renders a header per column and a row per datum, through both a plain accessor and a custom cell', () => {
    render(<DataTable columns={COLUMNS} data={DATA} />)
    expect(screen.getByRole('columnheader', { name: 'ID' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Nome' })).toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('ACME Srl')).toBeInTheDocument()
    expect(screen.getByText('#2')).toBeInTheDocument()
    expect(screen.getByText('Beta SpA')).toBeInTheDocument()
  })

  it('shows a distinguishable loading state instead of the table, not an empty one', () => {
    render(<DataTable columns={COLUMNS} data={[]} isLoading />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByText('Nessun risultato.')).not.toBeInTheDocument()
  })

  /** A different shape, deliberately -- but not a different *size*: bars shorter than
   *  the rows they stand in for, in no container, made the table jump the moment the
   *  request landed. */
  it('loads inside the same container as the table, at the height of a real row', () => {
    render(<DataTable columns={COLUMNS} data={[]} isLoading />)
    const container = screen.getByRole('status')
    expect(container.className).toContain('border-border')
    expect(container.querySelector('.h-12')).not.toBeNull()
  })

  it('shows an honest empty state -- the full table chrome, one row saying so -- once loading is over', () => {
    render(<DataTable columns={COLUMNS} data={[]} />)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('Nessun risultato.')).toBeInTheDocument()
  })

  it('accepts a caller-supplied empty message instead of the default', () => {
    render(<DataTable columns={COLUMNS} data={[]} emptyMessage="Nessun cliente trovato." />)
    expect(screen.getByText('Nessun cliente trovato.')).toBeInTheDocument()
  })

  /**
   * The defect a fix round caught live on Clienti, Persone and Deal alike: a
   * failed list request rendered the exact same "Nessun risultato." row an
   * honestly-empty result gets, with nothing on screen distinguishing "you
   * have none" from "we could not ask". `isError`/`error` exist to make the
   * second claim a visibly different shape, the same way `isLoading` already
   * is -- not a text swap inside the same row.
   */
  it('shows the failed request as a distinct banner, not the same shape an empty result gets', () => {
    const error = { code: 'http_error', detail: 'Il server non risponde.', status: 503 }
    render(<DataTable columns={COLUMNS} data={[]} isError error={error} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByText('Nessun risultato.')).not.toBeInTheDocument()
  })

  it('uses the server’s own message in the failed-request banner, not a client-side rewording', () => {
    const error = { code: 'not_found', detail: 'cliente 123 non trovato' }
    render(<DataTable columns={COLUMNS} data={[]} isError error={error} />)
    expect(screen.getByRole('alert')).toHaveTextContent('cliente 123 non trovato')
  })

  /**
   * A background refetch can fail while a previous, successful page is still
   * cached (`data` non-empty even though `isError` is true) -- this must keep
   * showing that stale-but-real data, not discard it for a banner over one
   * transient blip. Only the "nothing else to show" case (the test above)
   * should replace the table at all.
   */
  it('keeps showing already-loaded rows instead of a banner when a background refetch fails', () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={DATA}
        isError
        error={{ code: 'http_error', detail: 'Aggiornamento fallito.' }}
      />,
    )
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('ACME Srl')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('calls onRowClick with the original row datum, not a table-internal wrapper', async () => {
    const onRowClick = vi.fn()
    render(<DataTable columns={COLUMNS} data={DATA} onRowClick={onRowClick} />)
    await userEvent.click(screen.getByText('ACME Srl'))
    expect(onRowClick).toHaveBeenCalledExactlyOnceWith(ACME)
  })

  it('leaves rows out of tab order when no onRowClick is given -- nothing to activate', () => {
    render(<DataTable columns={COLUMNS} data={DATA} />)
    expect(rowFor('ACME Srl')).not.toHaveAttribute('tabindex')
  })

  it('puts a clickable row in tab order', () => {
    render(<DataTable columns={COLUMNS} data={DATA} onRowClick={vi.fn()} />)
    expect(rowFor('ACME Srl')).toHaveAttribute('tabindex', '0')
  })

  it('activates a row from the keyboard with Enter, mirroring a click', () => {
    const onRowClick = vi.fn()
    render(<DataTable columns={COLUMNS} data={DATA} onRowClick={onRowClick} />)
    fireEvent.keyDown(rowFor('Beta SpA'), { key: 'Enter' })
    expect(onRowClick).toHaveBeenCalledExactlyOnceWith(BETA)
  })

  it('activates a row from the keyboard with Space too', () => {
    const onRowClick = vi.fn()
    render(<DataTable columns={COLUMNS} data={DATA} onRowClick={onRowClick} />)
    fireEvent.keyDown(rowFor('Beta SpA'), { key: ' ' })
    expect(onRowClick).toHaveBeenCalledExactlyOnceWith(BETA)
  })

  it('ignores a key that is neither Enter nor Space', () => {
    const onRowClick = vi.fn()
    render(<DataTable columns={COLUMNS} data={DATA} onRowClick={onRowClick} />)
    fireEvent.keyDown(rowFor('Beta SpA'), { key: 'a' })
    expect(onRowClick).not.toHaveBeenCalled()
  })

  /* Half behavioural, like every responsive assertion in this suite: jsdom does not load
     the stylesheet and lays nothing out, so the classes are what can be read. They are
     worth reading anyway, because the defect they guard was invisible *by construction* --
     the row's only focus indicator used to be `--muted`, which since the Paper pass is the
     same tint the hover paints, so a keyboard row and a hovered row were the same pixel.
     `ring-inset` is load-bearing rather than stylistic: the scroll container above is
     `overflow-y-hidden` and an outer ring on the last row would be clipped away. */
  it('gives a clickable row a visible focus ring, drawn inside the clipping container', () => {
    render(<DataTable columns={COLUMNS} data={DATA} onRowClick={vi.fn()} />)
    const classes = rowFor('ACME Srl').className.split(/\s+/)
    expect(classes).toContain('focus-visible:ring-2')
    expect(classes).toContain('focus-visible:ring-ring')
    expect(classes).toContain('focus-visible:ring-inset')
    expect(classes).not.toContain('focus-visible:bg-muted')
  })

  it('leaves a non-clickable row without a focus ring -- there is nothing to focus', () => {
    render(<DataTable columns={COLUMNS} data={DATA} />)
    const classes = rowFor('ACME Srl').className.split(/\s+/)
    expect(classes).not.toContain('focus-visible:ring-2')
    expect(classes).not.toContain('focus-visible:ring-inset')
  })
})

/**
 * Numeric alignment is a property of the *column*, not of each cell: the header has to
 * sit over the digits it labels, and a `cell` renderer that right-aligned itself would
 * leave its own header on the left. So the column says `meta.align: 'right'` once and
 * both ends read it -- see `DataTableColumnMeta`'s own docstring for why `meta` is typed
 * through `tableFeatures({ columnMeta })` rather than by global declaration merging.
 */
describe('DataTable column meta', () => {
  const ALIGNED: ColumnDef<DataTableFeatures, Riga>[] = [
    { accessorKey: 'nome', header: 'Nome' },
    { accessorKey: 'id', header: 'Totale', meta: { align: 'right' } },
  ]

  it('right-aligns both the header and the cells of a numeric column', () => {
    render(<DataTable columns={ALIGNED} data={DATA} />)
    expect(screen.getByRole('columnheader', { name: 'Totale' }).className).toContain('text-right')
    const cell = screen.getByText('1').closest('td')
    expect(cell?.className).toContain('text-right')
  })

  it('leaves a column with no alignment of its own where it was', () => {
    render(<DataTable columns={ALIGNED} data={DATA} />)
    expect(screen.getByRole('columnheader', { name: 'Nome' }).className).not.toContain('text-right')
    expect(screen.getByText('ACME Srl').closest('td')?.className).not.toContain('text-right')
  })

  it('passes a column width through to the header, so the «⋯» column can stay narrow', () => {
    const sized: ColumnDef<DataTableFeatures, Riga>[] = [
      { accessorKey: 'nome', header: 'Nome' },
      { id: 'azioni', header: '', meta: { width: '3rem' } /* jsdom resolves rem against a 16px root */, cell: () => null },
    ]
    render(<DataTable columns={sized} data={DATA} />)
    const headers = screen.getAllByRole('columnheader')
    expect(headers[1]).toHaveStyle({ width: '48px' })
  })
})

describe('the table container', () => {
  /** The record of 2026-09-18: the table lives in a white container closed by a 1px
   *  ink line and no corner at all. The `rounded-xl` this used to assert named a radius
   *  the derived scale no longer gives; what the container must not carry is any
   *  radius class of its own. */
  it('is a square, hairline-bordered card that clips its own corners', () => {
    render(<DataTable columns={COLUMNS} data={DATA} />)
    const container = screen.getByRole('table').closest('[data-slot="data-table"]')
    expect(container?.className).not.toMatch(/rounded/)
    expect(container?.className).toContain('border-border')
    expect(container?.className).toContain('bg-card')
  })

  /**
   * A table wider than the panel has to scroll *inside its own box*, never widen the
   * page: at 390 the columns of Deal and of the settings panels reached past the right
   * edge of the white panel and took the page's own horizontal scroll with them
   * (screenshots `deal-lista-390.png`, `impostazioni-390.png`).
   *
   * `overflow-y-hidden` and not the old `overflow-hidden`: the vertical clip is what
   * keeps the first row's hover tint and the header's rule inside the box, and it has
   * to stay, but the horizontal axis is now a scroll axis.
   */
  it('scrolls a too-wide table inside itself rather than widening the page', () => {
    render(<DataTable columns={COLUMNS} data={DATA} />)
    const container = screen.getByRole('table').closest('[data-slot="data-table"]')
    expect(container?.className).toContain('overflow-x-auto')
    expect(container?.className).toContain('overflow-y-hidden')
  })
})
