import type { ColumnDef } from '@tanstack/react-table'
import { DateCell, MoneyCell, NumberCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
import { formatMoney as formatEuro } from '@rebase/ui/format'
import { formatIsoDateItalian } from '@/lib/dates'
import type { FieldDefinition } from '@/lib/schema'
import type { TimeEntry } from './queries'

const EMPTY = '—'

// `useGrouping: 'always'` on every formatter, never the ICU default: it-IT withholds
// the thousands separator until the integer part has five digits (checked directly on
// this stack's ICU: `Intl.NumberFormat('it-IT').format(2500.5)` renders "2500,50"). A
// four-figure total silently losing its separator on one screen is exactly the
// inconsistency this product forces grouping on to avoid -- `features/deals/columns.tsx`
// and `DynamicFieldRenderer`'s `renderFieldValue` already force it for the same reason.
const hours = new Intl.NumberFormat('it-IT', { useGrouping: 'always' })
// Six places, because a rate is Numeric(12,6): "33,333333 €/h" is not expressible at
// two, which is the whole reason the third scale exists (§6.1).
const rate = new Intl.NumberFormat('it-IT', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
  useGrouping: 'always',
})

/**
 * `null` is a real, distinct state -- an hour nobody has priced is not a free hour --
 * so it renders as a dash and never as `0,00 €`.
 *
 * `Number()` here is a *display* conversion of a value that has already stopped being
 * arithmetic: `lib/no-float-money.test.ts` bans the float only where a total could be
 * born from it, and every figure reaching these formatters was summed by the API. The
 * guard keys on API field names, so a parameter named `value` is deliberately outside
 * it -- what keeps that honest is that nothing in this module adds, subtracts or
 * compares; it only formats one already-final value at a time.
 */
export function formatMoneyValue(value: string | null): string {
  return value === null ? EMPTY : formatEuro(value)
}

export function formatHoursValue(value: string | null): string {
  return value === null ? EMPTY : hours.format(Number(value))
}

export function formatRateValue(value: string | null): string {
  return value === null ? EMPTY : `${rate.format(Number(value))} €/h`
}

const ORIGIN_LABELS: Record<string, string> = {
  manual: 'manual',
  deal: 'dal deal',
  user: "dall'utente",
  absent: 'absent',
}

/**
 * The rate with where it came from: «80,00 €/h (dal deal)».
 *
 * The origin next to the value, because "80 €/h" and "80 €/h, dal deal" answer two
 * different questions, and the second is the one somebody asks when the number looks
 * wrong. Extracted from the column so the accessor and the cell read the same figure
 * from one place instead of repeating the expression.
 */
function rateWithOrigin(entry: TimeEntry): string {
  if (entry.applied_rate === null) return EMPTY
  const origin = ORIGIN_LABELS[entry.rate_source] ?? entry.rate_source
  return `${formatRateValue(entry.applied_rate)} (${origin})`
}

/**
 * What an hour's «Stato» column can say. Unlike every other status in the product this
 * one is *derived*, not stored: the API sends `invoice_line_id` and `billable`, and
 * the three readings below are what those two fields mean together. Naming the three
 * gives the label and the tone one key to agree on, the way the stored enums have.
 */
export type TimeEntryStatus = 'invoiced' | 'to_invoice' | 'non_billable'

export const TIME_ENTRY_STATE_LABELS: Record<TimeEntryStatus, string> = {
  invoiced: 'Fatturata',
  to_invoice: 'Da fatturare',
  non_billable: 'Non fatturabile',
}

/**
 * `to_invoice` is gold for the same reason «Da incassare» is on the invoices table:
 * it is not an error and not a finished state, it is money waiting on somebody. An hour
 * already invoiced is settled (`ink`); one nobody will ever bill claims nothing about
 * money at all, so it stays quiet rather than reading as a problem -- most of these are
 * deliberate (internal work), not mistakes.
 */
export const TIME_ENTRY_STATE_TONE: Record<TimeEntryStatus, StatusTone> = {
  invoiced: 'ink',
  to_invoice: 'gold',
  non_billable: 'muted',
}

/** The two wire fields read as one of the three states. `billable` is optional on
 *  the wire, and an absent value is not a billable hour -- the same reading the
 *  previous inline ternary had. */
export function statusOf(entry: TimeEntry): TimeEntryStatus {
  if (entry.invoice_line_id !== null) return 'invoiced'
  return entry.billable ? 'to_invoice' : 'non_billable'
}

export function buildTimeEntryColumns(
  customFields: FieldDefinition[],
): ColumnDef<DataTableFeatures, TimeEntry>[] {
  const native: ColumnDef<DataTableFeatures, TimeEntry>[] = [
    {
      header: 'Data',
      id: 'data',
      accessorFn: (row) => formatIsoDateItalian(row.data),
      cell: ({ row }) => <DateCell value={row.original.data} />,
    },
    {
      header: 'Ore',
      id: 'hours',
      accessorFn: (row) => formatHoursValue(row.hours),
      // Hours are a figure, not money: right-aligned on tabular digits so a week of
      // them can be read down the units column, but through `NumberCell`.
      meta: { align: 'right' },
      cell: ({ row }) => <NumberCell>{formatHoursValue(row.original.hours)}</NumberCell>,
    },
    { header: 'Descrizione', accessorKey: 'description' },
    {
      header: 'Tariffa',
      id: 'applied_rate',
      accessorFn: (row) => rateWithOrigin(row),
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{rateWithOrigin(row.original)}</MoneyCell>,
    },
    {
      header: 'Valore',
      id: 'line_value',
      // Straight from the API. This is the number the timesheet prints next to the
      // entry, so it must be the server's own figure and not a product computed here.
      // `?? null` because the field is optional on the wire, and an absent value means
      // the same thing an explicit `null` does: nobody has priced this hour.
      accessorFn: (row) => formatMoneyValue(row.line_value ?? null),
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{formatMoneyValue(row.original.line_value ?? null)}</MoneyCell>,
    },
    {
      header: 'Stato',
      id: 'status',
      // Both maps are total over `TimeEntryStatus`, so neither read takes a fallback.
      accessorFn: (row) => TIME_ENTRY_STATE_LABELS[statusOf(row)],
      cell: ({ row }) => {
        const status = statusOf(row.original)
        return <StatusPill tone={TIME_ENTRY_STATE_TONE[status]}>{TIME_ENTRY_STATE_LABELS[status]}</StatusPill>
      },
    },
  ]

  // Prefixed `custom_` so a tenant-defined key can never collide with a native column's
  // id. The backend guard added in Task 4A-2 now refuses the collision at the source
  // too, but the prefix stays: it is what keeps the table correct for definitions
  // created before that guard existed.
  const custom: ColumnDef<DataTableFeatures, TimeEntry>[] = customFields.map((field) => ({
    header: field.label,
    id: `custom_${field.key}`,
    accessorFn: (row) => renderFieldValue(field, row.custom_fields[field.key]),
  }))

  return [...native, ...custom]
}
