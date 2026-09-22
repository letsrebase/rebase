import { tableFeatures, useTable, type ColumnDef, type RowData } from '@tanstack/react-table'
import type { KeyboardEvent } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Skeleton } from '@rebase/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { cn } from '@rebase/ui/cn'

// TanStack Table v9 (pinned exactly in package.json) is a from-scratch rewrite of
// v8, the API the original brief for this component was written against --
// `useReactTable`/`getCoreRowModel` do not exist in 9.0.0 at all (confirmed by
// reading node_modules/@tanstack/table-core's own .d.ts files, not assumed from an
// earlier major version's docs). A `useLegacyTable` v8-compatibility shim does
// exist, but it is explicitly `@deprecated` in its own type signature -- a bridge
// for migrating an *existing* v8 codebase, not a foundation to build brand-new,
// long-lived shared UI on. `tableFeatures({})` + `useTable` is the current,
// documented, non-deprecated way to ask for a plain table with none of v9's
// optional features (sorting, filtering, pagination, grouping, ...) switched on --
// exactly what every list this product has today needs, and the same shape
// TanStack's own "Quick Start" guide uses for this exact case.
/**
 * What a column may declare about its own presentation, typed through v9's own
 * `columnMeta` slot on `tableFeatures` rather than by declaration-merging the global
 * `ColumnMeta` interface. Both are supported (see `ExtractColumnMeta` in
 * @tanstack/table-core's ColumnDef.d.ts); the slot is chosen because declaration
 * merging would type `meta` for *every* table anywhere in the app, including a future
 * one built on different features, from a file nobody importing `ColumnDef` has any
 * reason to open. The phantom value is stripped at runtime -- only its type is used.
 *
 * `align` exists because numeric alignment belongs to the column, not the cell: the
 * header has to sit over the digits it labels, and a `cell` renderer that right-aligned
 * itself would leave its own header on the left. `width` is a plain CSS length passed
 * to the header cell, which is enough to keep the «⋯» column from taking a fair share
 * of the table's width -- v9's real column sizing is a feature (`columnSizingFeature`)
 * this table deliberately does not register. A percentage works too, and means the
 * opposite thing: in an auto-layout table a `100%` column is handed whatever the other
 * columns leave, which is how the invoice list's «Descrizione» column stays flexible
 * (`features/invoices/columns.tsx`).
 */
export interface DataTableColumnMeta {
  align?: 'left' | 'right'
  width?: string
}

const features = tableFeatures({ columnMeta: {} as DataTableColumnMeta })

/**
 * The `TFeatures` every `DataTable` column array is parameterised with: no
 * optional feature is registered, so `ColumnDef`'s feature-contributed options
 * (`enableSorting`, `filterFn`, ...) are not offered and cannot be set only to be
 * silently ignored. Exported so a caller can type its own `columns` array --
 * `ColumnDef<DataTableFeatures, Customer>[]` -- without reaching into
 * `@tanstack/react-table` for `tableFeatures` itself, or for `stockFeatures` (the
 * all-features-at-once shortcut the library's own v9 migration guide flags as a
 * bundle-size regression outside of migrating pre-existing v8 code) just to
 * satisfy this file's prop type.
 */
export type DataTableFeatures = typeof features

// `RowData` (table-core's own bound: `Record<string, any> | Array<any>`, see
// node_modules/.../@tanstack/table-core/dist/types/type-utils.d.ts) is what
// `ColumnDef`'s own `TData` parameter requires -- an unconstrained `<T>` here
// cannot be proven to satisfy it, since `T` could otherwise be instantiated with
// a primitive. Every real row this table renders (Customer, Person, Deal, ...) is
// already record-shaped, so this only makes an existing assumption explicit.
interface DataTableProps<T extends RowData> {
  columns: ColumnDef<DataTableFeatures, T>[]
  data: T[]
  isLoading?: boolean
  /** True when the query behind `data` failed. Distinct from an empty `data`
   *  array on purpose -- see this function's own docstring. */
  isError?: boolean
  /** The query's own error, handed to `QueryErrorBanner` verbatim. Only read
   *  when `isError` is true. */
  error?: unknown
  onRowClick?: (row: T) => void
  emptyMessage?: string
}

const LOADING_ROW_COUNT = 5

/**
 * One table for every list this product shows -- Clienti, Persone, Deal today,
 * whatever a later slice adds tomorrow.
 *
 * `isLoading`, "zero rows", and a failed request render as three deliberately
 * different shapes, not the same table with different text in one cell: a felt
 * sense of "something is happening" (animated skeleton bars, no header at all),
 * a real, completed, honestly-empty result (the full table chrome, one row
 * stating so in words), and "we do not actually know" (a banner, no table at
 * all). Collapsing any two of these into one state would let a slow network, or
 * a failed one, masquerade as "there is nothing here", which is a different
 * claim and not this component's to make on the caller's behalf -- confirmed as
 * a real, live defect on Clienti/Persone/Deal alike before this fix: every
 * column reading zero and "Nessun ..." is indistinguishable from a tenant that
 * genuinely has nothing, when the true state was "the request failed".
 *
 * `isError` only replaces the table when there is nothing else to show
 * (`data.length === 0`): a background refetch that fails while a previous,
 * successful page of `data` is still cached keeps showing that stale-but-real
 * data rather than discarding it for a banner -- the caller's own `data.data?.
 * items ?? []` fallback already collapses "never fetched" and "fetch failed
 * with nothing cached" into the same empty array by the time it reaches here,
 * which is exactly the case this component cannot tell apart from a genuine
 * empty result without `isError` naming it explicitly.
 */
export function DataTable<T extends RowData>({
  columns,
  data,
  isLoading,
  isError,
  error,
  onRowClick,
  emptyMessage = 'Nessun risultato.',
}: DataTableProps<T>) {
  const table = useTable({ features, columns, data })

  if (isLoading) {
    return (
      /* The same container, and bars the height of the rows they stand in for (`h-12`,
         `ui/table.tsx`), so the table does not visibly jump the moment the request
         lands. It is still a deliberately different *shape* -- animated bars, no header
         -- because "something is happening" is not "here is your data"; what it stops
         being is a different size. */
      <div
        data-slot="data-table"
        className="overflow-hidden border border-border bg-card p-2"
        role="status"
        aria-label="Caricamento"
      >
        <div className="space-y-2">
          {Array.from({ length: LOADING_ROW_COUNT }, (_, index) => (
            <Skeleton key={index} className="h-12 w-full" />
          ))}
        </div>
      </div>
    )
  }

  if (isError && data.length === 0) {
    return <QueryErrorBanner error={error} />
  }

  // Enter/Space activate a clickable row from the keyboard, mirroring what
  // `onClick` already gives the mouse: a bare `<tr onClick>` is only ever reachable
  // by a pointer, and every row below also gets `tabIndex={0}` so Tab reaches it in
  // the first place. This is the one shared table every future list in the product
  // renders through, so a gap here is not local to a single screen.
  function handleRowKeyDown(event: KeyboardEvent<HTMLTableRowElement>, row: T) {
    if (!onRowClick) return
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onRowClick(row)
  }

  return (
    /* The record's table: a white container closed by a 1px ink line and no corner at
       all -- the radius the `rounded-xl` this carried named has been zero since the
       application-variant record of 2026-09-18, and the class went with the comment
       that explained it. `overflow-y-hidden` is what keeps the first row's hover tint
       and the header's own bottom rule inside the box; the horizontal axis is a
       *scroll* axis instead, so a table with more columns than a phone is wide scrolls
       inside this box rather than widening the page around it (screenshots at 390 of
       2026-09-08). */
    <div
      data-slot="data-table"
      className="overflow-x-auto overflow-y-hidden border border-border bg-card"
    >
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const meta = header.column.columnDef.meta
                return (
                  <TableHead
                    key={header.id}
                    className={cn(meta?.align === 'right' && 'text-right')}
                    style={meta?.width === undefined ? undefined : { width: meta.width }}
                  >
                    {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                {emptyMessage}
              </TableCell>
            </TableRow>
          ) : (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                onClick={() => onRowClick?.(row.original)}
                onKeyDown={(event) => handleRowKeyDown(event, row.original)}
                tabIndex={onRowClick ? 0 : undefined}
                /* A focused row draws a ring, not a tint. The tint it used to draw was
                   `--muted`, which is the very colour the hover paints (`ui/table.tsx`),
                   so keyboard focus was both invisible on the white panel (~1.1:1) and
                   indistinguishable from the row under the pointer. `--ring` is
                   Watermelon, 3.9:1, past the 3:1 an indicator needs. `ring-inset` is
                   required rather than chosen: the container above clips on the vertical
                   axis (`overflow-y-hidden`), and an outer ring on the last row would be
                   cut off. */
                className={cn(
                  onRowClick &&
                    'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                )}
              >
                {row.getAllCells().map((cell) => (
                  <TableCell
                    key={cell.id}
                    className={cn(cell.column.columnDef.meta?.align === 'right' && 'text-right')}
                  >
                    <table.FlexRender cell={cell} />
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}
