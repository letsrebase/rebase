import type { ColumnDef } from '@tanstack/react-table'
import { EntityCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import type { LegalEntityRecord } from '@/lib/legalEntity'
import type { FieldDefinition } from '@/lib/schema'
import type { Customer } from './queries'

const EMPTY = '—'

/**
 * `null` and `""` both mean "nothing here" for a native text column -- unlike a
 * dynamic field (`renderFieldValue`'s job, in DynamicFieldRenderer.tsx), a native
 * column has no field type that could make `0`/`false` a legitimate value to
 * preserve, so the wider check is safe here and `??` alone is not: `CustomerForm`'s
 * own "clear a field" path sends an explicit `""` to clear a native *text* column
 * (`clearedNativeValue` in lib/schema.ts -- every column on this table is one), and
 * the cell then holds an empty string, not `null`. `??` alone would render that
 * legitimately-cleared value as a blank cell instead of the same dash every other
 * absent value gets -- reproduced live while testing the edit form, not a
 * theoretical gap. Both spellings have to be handled here regardless: since task
 * 4B-1 a `null` really can arrive from a clear as well.
 */
export function displayNative(value: string | null): string {
  return value === null || value === '' ? EMPTY : value
}

/**
 * TanStack Table v9 (pinned exactly in package.json) is a from-scratch rewrite of
 * v8: `ColumnDef` takes `<TFeatures, TData, TValue>`, not v8's `<TData, TValue>` --
 * confirmed by reading `@tanstack/table-core`'s own `ColumnDef.d.ts`, not assumed
 * from an earlier major version's docs. `DataTableFeatures` (DataTable.tsx's own
 * export) is the `TFeatures` every table in this product is instantiated with, so
 * this is the same type parameter `DataTable`'s own `columns` prop already requires
 * -- getting it right here is what lets `<DataTable columns={buildCustomerColumns(...)} />`
 * type-check at all.
 *
 * Native columns first, then one per custom field -- so a field defined at runtime
 * through `POST /api/field-definitions` shows up in this table with no code change,
 * no rebuild, no deploy. `customFields` already excludes archived definitions (see
 * `useEntitySchema`/`describe_specs`), so there is nothing here to filter a second
 * time.
 */
export interface CustomerColumnOptions {
  /**
   * The legal entities to name in an «Azienda» column after the name (REB-625, spec 2026-10-03
   * §5 «Lists»). Given only under «Tutte le aziende» from the second legal entity on
   * (`useLegalEntitiesToName`): with one legal entity, or with one selected, the column would say
   * the same thing on every row.
   */
  legal_entities?: LegalEntityRecord[]
}

export function buildCustomerColumns(
  customFields: FieldDefinition[],
  options: CustomerColumnOptions = {},
): ColumnDef<DataTableFeatures, Customer>[] {
  const legalEntityName = new Map((options.legal_entities ?? []).map((a) => [a.id, a.nome]))
  const legal_entity: ColumnDef<DataTableFeatures, Customer>[] = options.legal_entities
    ? [
        {
          header: 'Azienda',
          id: 'legal_entity',
          // The dash for an id the active list does not carry: a customer of a
          // deactivated azienda keeps its rows and its history (spec §1.8).
          accessorFn: (row) => legalEntityName.get(row.legal_entity_id) ?? EMPTY,
        },
      ]
    : []
  const native: ColumnDef<DataTableFeatures, Customer>[] = [
    {
      header: 'Ragione sociale',
      accessorKey: 'legal_name',
      // The row *is* a company, so the first cell carries its identity: an initials
      // chip beside the name (design spec §4). The accessor stays the bare name, so
      // the table still has one plain text value per cell to sort or export; `cell`
      // only adds the chip on top of it, the same shape «Azienda» in
      // `features/people/columns.tsx` already uses.
      cell: ({ row }) => <EntityCell name={row.original.legal_name} />,
    },
    ...legal_entity,
    { header: 'P.IVA', id: 'vat_number', accessorFn: (row) => displayNative(row.vat_number) },
    { header: 'Comune', id: 'city', accessorFn: (row) => displayNative(row.city) },
    { header: 'Email', id: 'email', accessorFn: (row) => displayNative(row.email) },
    { header: 'Telefono', id: 'phone', accessorFn: (row) => displayNative(row.phone) },
  ]

  // Prefixed `custom_` id so a tenant-defined key can never collide with a native
  // column's own id (`vat_number`, `city`, ...) even in the one case that would
  // otherwise be ambiguous: nothing today stops a custom field from being *named*
  // the same as a native column (FieldDefinitionService.create only checks for a
  // collision against other field definitions, not against native columns).
  const custom: ColumnDef<DataTableFeatures, Customer>[] = customFields.map((field) => ({
    header: field.label,
    id: `custom_${field.key}`,
    // `renderFieldValue`, not `value ?? EMPTY`: a custom field can be numeric,
    // currency or checkbox, where `0`/`false` are real values, not absent ones --
    // `renderFieldValue` already draws that line correctly (see its own docstring),
    // so this column reuses it instead of re-deciding it here.
    accessorFn: (row) => renderFieldValue(field, row.custom_fields[field.key]),
  }))

  return [...native, ...custom]
}
