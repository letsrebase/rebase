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
  manuale: 'manuale',
  deal: 'dal deal',
  utente: "dall'utente",
  assente: 'assente',
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
  if (entry.tariffa_applicata === null) return EMPTY
  const origin = ORIGIN_LABELS[entry.tariffa_origine] ?? entry.tariffa_origine
  return `${formatRateValue(entry.tariffa_applicata)} (${origin})`
}

/**
 * What an hour's «Stato» column can say. Unlike every other status in the product this
 * one is *derived*, not stored: the API sends `invoice_line_id` and `fatturabile`, and
 * the three readings below are what those two fields mean together. Naming the three
 * gives the label and the tone one key to agree on, the way the stored enums have.
 */
export type TimeEntryStato = 'fatturata' | 'da_fatturare' | 'non_fatturabile'

export const TIME_ENTRY_STATE_LABELS: Record<TimeEntryStato, string> = {
  fatturata: 'Fatturata',
  da_fatturare: 'Da fatturare',
  non_fatturabile: 'Non fatturabile',
}

/**
 * `da_fatturare` is gold for the same reason «Da incassare» is on the invoices table:
 * it is not an error and not a finished state, it is money waiting on somebody. An hour
 * already invoiced is settled (`ink`); one nobody will ever bill claims nothing about
 * money at all, so it stays quiet rather than reading as a problem -- most of these are
 * deliberate (internal work), not mistakes.
 */
export const TIME_ENTRY_STATE_TONE: Record<TimeEntryStato, StatusTone> = {
  fatturata: 'ink',
  da_fatturare: 'gold',
  non_fatturabile: 'muted',
}

/** The two wire fields read as one of the three states. `fatturabile` is optional on
 *  the wire, and an absent value is not a billable hour -- the same reading the
 *  previous inline ternary had. */
export function statoOf(entry: TimeEntry): TimeEntryStato {
  if (entry.invoice_line_id !== null) return 'fatturata'
  return entry.fatturabile ? 'da_fatturare' : 'non_fatturabile'
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
      id: 'ore',
      accessorFn: (row) => formatHoursValue(row.ore),
      // Hours are a figure, not money: right-aligned on tabular digits so a week of
      // them can be read down the units column, but through `NumberCell`.
      meta: { align: 'right' },
      cell: ({ row }) => <NumberCell>{formatHoursValue(row.original.ore)}</NumberCell>,
    },
    { header: 'Descrizione', accessorKey: 'descrizione' },
    {
      header: 'Tariffa',
      id: 'tariffa_applicata',
      accessorFn: (row) => rateWithOrigin(row),
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{rateWithOrigin(row.original)}</MoneyCell>,
    },
    {
      header: 'Valore',
      id: 'valore_riga',
      // Straight from the API. This is the number the timesheet prints next to the
      // entry, so it must be the server's own figure and not a product computed here.
      // `?? null` because the field is optional on the wire, and an absent value means
      // the same thing an explicit `null` does: nobody has priced this hour.
      accessorFn: (row) => formatMoneyValue(row.valore_riga ?? null),
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{formatMoneyValue(row.original.valore_riga ?? null)}</MoneyCell>,
    },
    {
      header: 'Stato',
      id: 'stato',
      // Both maps are total over `TimeEntryStato`, so neither read takes a fallback.
      accessorFn: (row) => TIME_ENTRY_STATE_LABELS[statoOf(row)],
      cell: ({ row }) => {
        const stato = statoOf(row.original)
        return <StatusPill tone={TIME_ENTRY_STATE_TONE[stato]}>{TIME_ENTRY_STATE_LABELS[stato]}</StatusPill>
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
