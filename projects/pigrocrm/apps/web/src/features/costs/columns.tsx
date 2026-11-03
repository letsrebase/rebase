import type { ColumnDef } from '@tanstack/react-table'
import { DateCell, MoneyCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { formatMoneyValue } from '@/features/time/columns'
import { formatIsoDateItalian } from '@/lib/dates'
import type { FieldDefinition } from '@/lib/schema'
import type { Cost, CostCategory } from './queries'

const EMPTY = '—'

export function buildCostColumns(
  categories: CostCategory[],
  customFields: FieldDefinition[],
): ColumnDef<DataTableFeatures, Cost>[] {
  const names = new Map(categories.map((category) => [category.id, category.nome]))
  const native: ColumnDef<DataTableFeatures, Cost>[] = [
    {
      header: 'Data',
      id: 'data',
      accessorFn: (row) => formatIsoDateItalian(row.data),
      // `DateCell` formats the raw ISO value through the same `lib/dates.ts` this
      // accessor uses, and adds the calendar icon (design spec §4).
      cell: ({ row }) => <DateCell value={row.original.data} />,
    },
    {
      header: 'Categoria',
      id: 'category_id',
      // Resolved from the list rather than embedded in the row: a category can be
      // renamed, and an archived one still has to show its name here, which is exactly
      // why archiving replaces deletion -- and why the panel loads the archived ones
      // too. The dash is only ever "the caller did not hand me this category", never
      // "it does not exist": the backend has no way to delete one.
      accessorFn: (row) => names.get(row.category_id) ?? EMPTY,
    },
    { header: 'Descrizione', accessorKey: 'description' },
    { header: 'Fornitore', id: 'supplier', accessorFn: (row) => row.supplier ?? EMPTY },
    {
      header: 'Importo',
      id: 'amount',
      // Straight from the API string. A negative amount is a refund or a credit note
      // received (§4.4), so it is labelled rather than treated as an error.
      accessorFn: (row) =>
        `${formatMoneyValue(row.amount)}${row.amount.startsWith('-') ? ' (rimborso)' : ''}`,
      meta: { align: 'right' },
      cell: ({ row }) => (
        <MoneyCell>
          {formatMoneyValue(row.original.amount)}
          {row.original.amount.startsWith('-') ? ' (rimborso)' : ''}
        </MoneyCell>
      ),
    },
    {
      header: 'Giustificativo',
      id: 'document_id',
      accessorFn: (row) => (row.document_id === null ? EMPTY : 'allegato'),
    },
  ]

  // Prefixed `custom_` for the same reason `features/time/columns.tsx` does it: a
  // tenant-defined key must never collide with a native column's id, including for
  // definitions created before the backend started refusing the collision.
  const custom: ColumnDef<DataTableFeatures, Cost>[] = customFields.map((field) => ({
    header: field.label,
    id: `custom_${field.key}`,
    accessorFn: (row) => renderFieldValue(field, row.custom_fields[field.key]),
  }))

  return [...native, ...custom]
}
