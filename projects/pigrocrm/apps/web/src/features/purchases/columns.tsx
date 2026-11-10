import type { ColumnDef } from '@tanstack/react-table'
import { DateCell, MoneyCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { StatusPill } from '@/components/StatusPill'
import { formatMoneyValue } from '@/features/time/columns'
import { formatIsoDateItalian } from '@/lib/dates'
import type { FieldDefinition } from '@/lib/schema'
import { KIND_LABELS } from './formValues'
import { SupplierName } from './SupplierName'
import type { CostCategory, Purchase } from './queries'

const EMPTY = '—'

/** A negative total on a receipt or an expense is a refund (§4.4), so it is labelled rather
 *  than treated as an error. On a credit note the minus is the document's own sign and
 *  needs no word. */
function isRefund(row: Purchase): boolean {
  return (row.kind === 'receipt' || row.kind === 'expense') && row.amount.startsWith('-')
}

export function buildPurchaseColumns(
  categories: CostCategory[],
  customFields: FieldDefinition[],
): ColumnDef<DataTableFeatures, Purchase>[] {
  const names = new Map(categories.map((category) => [category.id, category.name]))
  const native: ColumnDef<DataTableFeatures, Purchase>[] = [
    {
      header: 'Data',
      id: 'date',
      accessorFn: (row) => formatIsoDateItalian(row.date),
      // `DateCell` formats the raw ISO value through the same `lib/dates.ts` this
      // accessor uses, and adds the calendar icon (design spec §4).
      cell: ({ row }) => <DateCell value={row.original.date} />,
    },
    {
      header: 'Tipo',
      id: 'kind',
      accessorFn: (row) => KIND_LABELS[row.kind],
      // An annulled purchase stays on the register: its muted pill says it no longer counts.
      cell: ({ row }) => (
        <span className="inline-flex items-center gap-2">
          {KIND_LABELS[row.original.kind]}
          {row.original.status === 'cancelled' && <StatusPill tone="muted">Annullato</StatusPill>}
        </span>
      ),
    },
    {
      header: 'Fornitore',
      id: 'supplier',
      accessorFn: (row) => row.merchant_name ?? EMPTY,
      cell: ({ row }) =>
        row.original.supplier_id !== null ? (
          <SupplierName id={row.original.supplier_id} />
        ) : (
          (row.original.merchant_name ?? EMPTY)
        ),
    },
    { header: 'Numero', id: 'number', accessorFn: (row) => row.number ?? EMPTY },
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
    {
      header: 'Totale',
      id: 'amount',
      // Straight from the API string, IVA included.
      accessorFn: (row) => `${formatMoneyValue(row.amount)}${isRefund(row) ? ' (rimborso)' : ''}`,
      meta: { align: 'right' },
      cell: ({ row }) => (
        <MoneyCell>
          {formatMoneyValue(row.original.amount)}
          {isRefund(row.original) ? ' (rimborso)' : ''}
        </MoneyCell>
      ),
    },
    {
      // What the purchase costs once the VAT it recovers is out (frozen when registered):
      // the figure the profit and loss reads, and not the total above.
      header: 'Costo',
      id: 'cost_amount',
      accessorFn: (row) => formatMoneyValue(row.cost_amount),
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{formatMoneyValue(row.original.cost_amount)}</MoneyCell>,
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
  const custom: ColumnDef<DataTableFeatures, Purchase>[] = customFields.map((field) => ({
    header: field.label,
    id: `custom_${field.key}`,
    accessorFn: (row) => renderFieldValue(field, row.custom_fields[field.key]),
  }))

  return [...native, ...custom]
}
