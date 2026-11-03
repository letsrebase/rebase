import type { ColumnDef } from '@tanstack/react-table'
import { DateCell, EntityCell, MoneyCell, NumberCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { formatIsoDateItalian } from '@/lib/dates'
import { formatMoney as formatEuro } from '@rebase/ui/format'
import { decimalStringFromScaled, MONEY_SCALE, scaledFromDecimalString } from '@/lib/decimal'
import type { FieldDefinition } from '@/lib/schema'
import type { Deal } from './queries'

const EMPTY = '—'

// `useGrouping: 'always'` on the number formatter, not the ICU default: it-IT withholds
// the thousands separator until the integer part has five digits (checked directly on
// this stack's ICU: `Intl.NumberFormat('it-IT').format(2500.5)` renders "2500,50"). The
// shared `formatMoney` forces it for euros; `KanbanBoard.test.tsx` asserts the separator.
const number = new Intl.NumberFormat('it-IT', { useGrouping: 'always' })

/**
 * `null` and `""` both mean "nothing here" for a native text column -- identical
 * reasoning to `features/customers/columns.tsx`/`features/people/columns.tsx`'s
 * own `displayNative` (see either file's docstring for the live bug this guards).
 * Both spellings reach a cell: a cleared native *text* column holds `""`, and since
 * task 4B-1 a cleared numeric or date column holds `null` -- see
 * `clearedNativeValue` in lib/schema.ts. Kept local rather than imported, matching
 * those two files' own precedent of a small per-feature display helper rather than
 * a shared module.
 */
export function displayNative(value: string | null): string {
  return value === null || value === '' ? EMPTY : value
}

/**
 * A Deal-specific currency formatter, not `renderFieldValue`: `expected_value`/
 * `budgeted_value` are native `Decimal` columns (deals/models.py), not a
 * tenant-defined custom field, so there is no `FieldDefinition` to hand
 * `renderFieldValue`. `null` is a real, absent state (a deal nobody has priced
 * yet yields `None`); an empty string cannot occur here the way it can for a
 * native text column -- `DealUpdate` has no supported way to write `""` into a
 * `Decimal` field at all (see `sumExpectedValue`'s docstring below for the one
 * place this matters on this screen).
 */
export function formatMoney(value: string | null): string {
  return value === null ? EMPTY : formatEuro(value)
}

/**
 * The deal's value as a list or a card shows it: the total, and `+ 500 € /mese` beside it
 * when the deal has a monthly recurring figure. Zero is compared as scaled integer cents,
 * never as a float, and the server's figures are only formatted here, never combined.
 */
export function formatDealValue(deal: Pick<Deal, 'expected_value' | 'monthly_recurring_value'>): string {
  const total = formatMoney(deal.expected_value)
  const monthly = deal.monthly_recurring_value
  if (monthly == null || scaledFromDecimalString(monthly, MONEY_SCALE) === 0) return total
  return `${total} + ${formatEuro(monthly)}/mese`
}

/** Same reasoning as `formatMoney`, for the one native column that is a plain
 *  quantity rather than money: `budgeted_hours` (`Numeric(8, 2)`, deals/
 *  models.py). Kept distinct from `formatMoney` rather than reusing it with a
 *  flag: a value here is never a euro amount, and `renderFieldValue`'s own
 *  `'number'` case (DynamicFieldRenderer.tsx) uses the identical formatter for
 *  the identical reason on a custom numeric field. */
export function formatHours(value: string | null): string {
  return value === null ? EMPTY : number.format(Number(value))
}

export function formatDate(value: string | null): string {
  return value === null ? EMPTY : formatIsoDateItalian(value)
}

/**
 * The Kanban column total: every deal's `expected_value` in the stage, summed
 * in exact integer cents and divided back to euros exactly once, purely for
 * display -- never re-summed as a float. A `null` (unpriced) deal contributes
 * nothing, the same way `formatMoney` shows it as a dash rather than as zero.
 *
 * The cents conversion used to live here as a private `centsFromDecimalString`;
 * it is now `lib/decimal.ts`'s `scaledFromDecimalString`, unchanged in behaviour
 * (this file's own 300-deal assertion in `columns.test.ts` is what proves that)
 * but generalised over the scale, because slice 4's week grid and costs panel
 * need the identical arithmetic on hours and on rates. A second copy is how two
 * screens start disagreeing about the same euro; `lib/decimal.ts`'s docstring
 * carries the full reasoning for why money never touches a float here.
 */
export function sumExpectedValue(deals: Deal[]): string {
  const totalCents = deals.reduce(
    (sum, deal) =>
      sum +
      (deal.expected_value === null
        ? 0
        : scaledFromDecimalString(deal.expected_value, MONEY_SCALE)),
    0,
  )
  return formatEuro(decimalStringFromScaled(totalCents, MONEY_SCALE))
}

/**
 * TanStack Table v9 (pinned exactly in package.json): `ColumnDef` takes
 * `<TFeatures, TData, TValue>`, not v8's `<TData, TValue>` -- see `features/
 * customers/columns.tsx`'s identical comment for how this was confirmed against
 * `@tanstack/table-core`'s own types. `DataTableFeatures` (`DataTable.tsx`'s own
 * export) is the same `TFeatures` every table in this product is instantiated
 * with.
 *
 * Native columns first, then one per custom field, matching Customer/Person's
 * own convention: a field defined at runtime through `POST /api/field-
 * definitions` shows up here with no code change. `customFields` already
 * excludes archived definitions (`useEntitySchema`/`describe_specs`), so there is
 * nothing here to filter a second time. `budgeted_hours`/`budgeted_value`
 * are deliberately not table columns, matching the detail route's own
 * "Preventivo" card (`routes/app/deal/$dealId.tsx`): they are written now but
 * not read until the slice 4 estimate-vs-actual report.
 */
export function buildDealColumns(
  customFields: FieldDefinition[],
): ColumnDef<DataTableFeatures, Deal>[] {
  const native: ColumnDef<DataTableFeatures, Deal>[] = [
    {
      header: 'Nome',
      accessorKey: 'name',
      // The first cell carries the deal's identity as a chip, with the customer on the
      // line under it (design spec §4) -- which is what turns a list of project names
      // into a list of work. It costs no request per row: `DealRead` carries
      // `customer_legal_name`, filled by one batched lookup per page, precisely so
      // this cell could exist (see `DealService._reads`).
      cell: ({ row }) => (
        <EntityCell name={row.original.name} sub={row.original.customer_legal_name ?? null} />
      ),
    },
    {
      header: 'Valore previsto',
      id: 'expected_value',
      accessorFn: (row) => formatDealValue(row),
      // `meta.align` right-aligns the header over the digits too; `MoneyCell` alone
      // could only align what is inside the cell.
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{formatDealValue(row.original)}</MoneyCell>,
    },
    {
      header: 'Probabilità',
      id: 'probability',
      accessorFn: (row) => `${row.probability}%`,
      // A percentage is a number: it belongs on the right with the money, so a column
      // of them can be scanned down the units digit. `meta.align` right-aligns the
      // header over the digits; `NumberCell` gives the digits themselves the tabular
      // figures that make a column of them line up.
      meta: { align: 'right' },
      cell: ({ row }) => <NumberCell>{row.original.probability}%</NumberCell>,
    },
    {
      header: 'Chiusura prevista',
      id: 'expected_close_date',
      // `DateCell` formats the raw ISO value through the same `lib/dates.ts`
      // `formatDate` above calls, and adds the calendar icon.
      accessorFn: (row) => formatDate(row.expected_close_date),
      cell: ({ row }) => <DateCell value={row.original.expected_close_date} />,
    },
  ]

  // Prefixed `custom_` id so a tenant-defined key can never collide with a
  // native column's own id -- identical reasoning (and identical residual gap:
  // `FieldDefinitionService.create` does not itself guard against this) as
  // `features/customers/columns.tsx`/`features/people/columns.tsx`.
  const custom: ColumnDef<DataTableFeatures, Deal>[] = customFields.map((field) => ({
    header: field.label,
    id: `custom_${field.key}`,
    accessorFn: (row) => renderFieldValue(field, row.custom_fields[field.key]),
  }))

  return [...native, ...custom]
}
