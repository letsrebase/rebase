import { daysBetweenIso, isoYearMonth } from '@/lib/dates'
import type { TaxCalendarEntry } from './labels'
import type { TaxDueItem } from './queries'

/**
 * The pack's tax dates as the Home and «Tasse» draw them (REB-1026). Nothing here is an amount:
 * a date, a count of days between two dates, a label. The pack is law and is the server's;
 * what these functions know is how to say its rows in Italian and how to group the rows that
 * fall on one day.
 */

type Kind = TaxCalendarEntry['kind']
type TaxName = TaxCalendarEntry['tax']

/** What a date is the date of, by the kind the pack gives it. A function of the date's own
 *  calendar year, because the June payment serves two tax years («Saldo 2025 e primo acconto
 *  2026», brief «Inferred»); a record, so a new kind in the pack fails the build here. */
const KIND_LABELS: Record<Kind, (year: number, month: number) => string> = {
  periodic_payment: (_year, month) => `IVA di ${monthName(month - 1)}`,
  quarterly_payment: () => 'IVA trimestrale',
  annual_balance: (year) => `Saldo IVA ${year - 1}`,
  advance: (year) => `Acconto IVA ${year}`,
  balance_and_first_advance: (year) => `Saldo ${year - 1} e primo acconto ${year}`,
  second_advance: (year) => `Secondo acconto ${year}`,
  instalments_end: (year) => `Ultima rata di saldo ${year - 1} e primo acconto ${year}`,
  withholding_payment: (_year, month) => `Ritenute di ${monthName(month - 1)}`,
  annual_fee: (year) => `Diritto annuale CCIAA ${year}`,
}

const TAX_NAMES: Record<TaxName, string> = {
  iva: 'IVA',
  income_tax: 'Imposte sui redditi',
  substitute_tax: 'Imposta sostitutiva',
  ires: 'IRES',
  irap: 'IRAP',
  withholding: 'Ritenute',
  chamber_fee: 'Camera di commercio',
}

const MONTHS = [
  'dicembre',
  'gennaio',
  'febbraio',
  'marzo',
  'aprile',
  'maggio',
  'giugno',
  'luglio',
  'agosto',
  'settembre',
  'ottobre',
  'novembre',
  'dicembre',
]

/** The month's name, 1 to 12; 0 is the December before. */
function monthName(month: number): string {
  return MONTHS[month] ?? ''
}

/** «Saldo 2025 e primo acconto 2026»: the line's own words, from its kind and nominal day. */
export function dateLabel(entry: TaxCalendarEntry): string {
  const parts = isoYearMonth(entry.nominal_date)
  return parts === null ? '' : KIND_LABELS[entry.kind](parts.year, parts.month)
}

/** «Imposta sostitutiva», «IVA», «Imposte sui redditi»: the tax a date belongs to. */
export function taxOfDate(entry: TaxCalendarEntry): string {
  return TAX_NAMES[entry.tax]
}

/** The taxes whose labels already say what they are: no suffix beside them. */
export const NAMES_ITSELF: ReadonlySet<TaxName> = new Set<TaxName>(['iva', 'withholding', 'chamber_fee'])

/** What a line says on the Home, where there is no column for the tax: the label, then the tax
 *  it belongs to unless the label already says IVA. */
export function lineText(entry: TaxCalendarEntry): string {
  return NAMES_ITSELF.has(entry.tax) ? dateLabel(entry) : `${dateLabel(entry)} · ${taxOfDate(entry)}`
}

/** Whether a due item is the payment a pack line announces: the same tax, the pack's «imposte sui
 *  redditi» being a person's IRPEF. A line the items do not cover stays, so a tax with no item that
 *  day (no estimate yet) is still said to fall due. */
function covers(item: TaxDueItem, entry: TaxCalendarEntry): boolean {
  return item.tax === (entry.tax === 'income_tax' ? 'irpef' : entry.tax)
}

/** One day on which something falls due: the pack's lines and, for the taxes whose payments are
 *  due items, the items themselves (a balance, an advance, an instalment, the chamber fee). */
export interface DueDay {
  date: string
  entries: TaxCalendarEntry[]
  items: TaxDueItem[]
}

/** The entries of one calendar and the due items of the same year, grouped by the day they fall
 *  due, in time order. A day with items drops the pack's lines of the taxes the items cover. */
export function groupByDueDate(
  entries: readonly TaxCalendarEntry[],
  items: readonly TaxDueItem[] = [],
): DueDay[] {
  const days = new Map<string, DueDay>()
  const day = (date: string): DueDay => {
    let found = days.get(date)
    if (!found) {
      found = { date, entries: [], items: [] }
      days.set(date, found)
    }
    return found
  }
  for (const item of items) if (item.due_date !== null) day(item.due_date).items.push(item)
  for (const entry of entries) {
    const found = day(entry.due_date)
    if (!found.items.some((item) => covers(item, entry))) found.entries.push(entry)
  }
  return [...days.values()].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
}

/** The first `limit` days on or after `today` that carry a date. */
export function nextDays(entries: readonly TaxCalendarEntry[], today: string, limit: number): DueDay[] {
  return groupByDueDate(entries.filter((entry) => entry.due_date >= today)).slice(0, limit)
}

/** «tra 6 giorni», «oggi», «domani», «3 giorni fa»: a difference of two dates, never an amount. */
export function distanceLabel(today: string, date: string): string {
  const days = daysBetweenIso(today, date)
  if (days === null) return ''
  if (days === 0) return 'oggi'
  if (days === 1) return 'domani'
  if (days === -1) return 'ieri'
  return days > 0 ? `tra ${days} giorni` : `${-days} giorni fa`
}
