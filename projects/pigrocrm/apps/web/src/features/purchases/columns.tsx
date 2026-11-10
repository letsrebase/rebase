import type { ColumnDef } from '@tanstack/react-table'
import { DateCell, MoneyCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { StatusPill } from '@/components/StatusPill'
import { cn } from '@rebase/ui/cn'
import { formatMoneyValue } from '@/features/time/columns'
import { formatIsoDateItalian } from '@/lib/dates'
import type { FieldDefinition } from '@/lib/schema'
import type { LegalEntityRecord } from '@/lib/legalEntity'
import { KIND_LABELS } from './formValues'
import { documentLabel } from './labels'
import { AmountCell, SupplierDocumentCell } from './PurchaseCells'
import { SupplierName } from './SupplierName'
import type { CostCategory, Purchase } from './queries'

const EMPTY = '—'

/** A negative total on a receipt or an expense is a refund (§4.4), so it is labelled rather
 *  than treated as an error. On a credit note the minus is the document's own sign and
 *  needs no word. */
function isRefund(row: Purchase): boolean {
  return (row.kind === 'receipt' || row.kind === 'expense') && row.amount.startsWith('-')
}

export interface PurchaseColumnOptions {
  /** Draws the register of «Acquisti»: the signature «Fornitore e documento» cell, the category,
   *  the date and the amount, in place of the panel's flat columns. */
  withSupplierDocument?: boolean
  /** The legal entities to name in an «Azienda» column (`useLegalEntitiesToName`): only under
   *  «Tutte le aziende» from the second one on. */
  legal_entities?: LegalEntityRecord[]
}

function buildRegisterColumns(
  categories: CostCategory[],
  customFields: FieldDefinition[],
  options: PurchaseColumnOptions,
): ColumnDef<DataTableFeatures, Purchase>[] {
  const names = new Map(categories.map((category) => [category.id, category.name]))
  const entityNames = new Map((options.legal_entities ?? []).map((entity) => [entity.id, entity.name]))
  const muted = (row: Purchase) => row.status === 'cancelled'
  const columns: ColumnDef<DataTableFeatures, Purchase>[] = [
    {
      header: 'Fornitore e documento',
      id: 'supplier_document',
      accessorFn: (row) => `${row.merchant_name ?? ''} ${documentLabel(row)}`.trim(),
      cell: ({ row }) => <SupplierDocumentCell purchase={row.original} />,
    },
  ]
  if (options.legal_entities !== undefined) {
    columns.push({
      header: 'Azienda',
      id: 'legal_entity',
      // «Tutte» for a shared purchase (no legal entity of its own); the dash for one the active
      // list does not carry (a deactivated legal entity keeps its rows).
      accessorFn: (row) =>
        row.legal_entity_id === null ? 'Tutte' : (entityNames.get(row.legal_entity_id) ?? EMPTY),
    })
  }
  columns.push(
    {
      header: 'Categoria',
      id: 'category_id',
      accessorFn: (row) => names.get(row.category_id) ?? EMPTY,
      cell: ({ row }) => (
        <span className={cn(muted(row.original) && 'text-muted-foreground')}>
          {names.get(row.original.category_id) ?? EMPTY}
        </span>
      ),
    },
    {
      header: 'Data',
      id: 'date',
      accessorFn: (row) => formatIsoDateItalian(row.date),
      cell: ({ row }) => (
        <span className={cn(muted(row.original) && 'text-muted-foreground')}>
          <DateCell value={row.original.date} />
        </span>
      ),
    },
    {
      header: 'Importo',
      id: 'amount',
      accessorFn: (row) => formatMoneyValue(row.amount),
      meta: { align: 'right' },
      cell: ({ row }) => <AmountCell purchase={row.original} />,
    },
  )
  return [...columns, ...customColumns(customFields)]
}

/** Prefixed `custom_` for the same reason `features/time/columns.tsx` does it: a tenant-defined
 *  key must never collide with a native column's id. */
function customColumns(customFields: FieldDefinition[]): ColumnDef<DataTableFeatures, Purchase>[] {
  return customFields.map((field) => ({
    header: field.label,
    id: `custom_${field.key}`,
    accessorFn: (row) => renderFieldValue(field, row.custom_fields[field.key]),
  }))
}

export function buildPurchaseColumns(
  categories: CostCategory[],
  customFields: FieldDefinition[],
  options: PurchaseColumnOptions = {},
): ColumnDef<DataTableFeatures, Purchase>[] {
  if (options.withSupplierDocument === true) {
    return buildRegisterColumns(categories, customFields, options)
  }
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

  return [...native, ...customColumns(customFields)]
}
