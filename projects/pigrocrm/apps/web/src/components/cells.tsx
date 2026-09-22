import { Calendar } from 'lucide-react'
import type { ReactNode } from 'react'
import { formatIsoDateItalian } from '@/lib/dates'

/** The same em dash every absent value in this product renders as -- `displayNative`
 *  in the three feature column files, `formatMoney`/`formatDate` in the formatters. */
const EMPTY = '—'

/** A wire value that is a calendar day and nothing more. */
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

/** Day, month, year and the time of day, for a value that carries one. Matches the
 *  formatter `features/tokens/TokensPanel.tsx` already used for «Ultimo uso»: on a
 *  token the *hour* is the answer, not decoration. */
const instantFormatter = new Intl.DateTimeFormat('it-IT', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/**
 * A date shown with exactly as much precision as the wire value carries, which is the
 * one rule that lets both shapes the API sends through the same cell.
 *
 * A bare `YYYY-MM-DD` goes through `lib/dates.ts` -- the one module that knows
 * `new Date('2026-01-01')` is UTC midnight and formats a day early anywhere behind
 * Greenwich -- and renders as `gg/mm/aaaa`. A timestamp is a real instant, so
 * `new Date` is the *correct* parse for it (there is no calendar day to lose) and it
 * keeps its time of day. Anything that parses as neither is returned untouched rather
 * than rendered as `Invalid Date`.
 */
function italianDate(value: string): string {
  if (DATE_ONLY.test(value)) return formatIsoDateItalian(value)
  const instant = new Date(value)
  if (Number.isNaN(instant.getTime())) return value
  return instantFormatter.format(instant)
}

/**
 * A date, with the small calendar icon the reference puts before every one (design
 * spec §4).
 *
 * Takes the API's own string, not a pre-formatted one, so that "how a date is written"
 * and "what an absent date looks like" are each decided once instead of once per table.
 * Every date column in the product already formatted through an equivalent of
 * `italianDate` above, so the rendered string is unchanged by this cell; what changes is
 * that the icon exists.
 *
 * An absent date carries no icon: a calendar beside a dash claims there is a date.
 * `''` counts as absent for the same reason `displayNative` treats it so -- a cleared
 * native column can hold either spelling. `absent` overrides the em dash for the columns
 * where "nothing here" has a better word: a token that has never been used reads «mai»,
 * which says something the dash does not.
 */
export function DateCell({ value, absent = EMPTY }: { value: string | null; absent?: string }) {
  if (value === null || value === '') return <span className="text-muted-foreground">{absent}</span>
  return (
    <span className="inline-flex items-center gap-1.5">
      <Calendar aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
      {italianDate(value)}
    </span>
  )
}

/**
 * A money figure, right-aligned on tabular digits so a column of amounts lines up on
 * the cent.
 *
 * Takes the *formatted* figure, not a decimal string: the feature that owns the number
 * owns its formatting, and those formatters legitimately differ (`features/invoices/
 * format.ts` parses the API string into integer cents; `features/time/columns.tsx` and
 * `features/deals/columns.tsx` keep their own, each with its own `null` meaning). A
 * second money formatter in shared table code is how two screens start disagreeing
 * about the same euro -- and `lib/no-float-money.test.ts` exists because the tempting
 * shortcut is a float. So this cell adds no arithmetic of any kind.
 *
 * Alignment still needs the column's `meta.align: 'right'` for the *header* to sit over
 * the digits: see `DataTableColumnMeta`. This class is what keeps the figure hard right
 * inside the cell even when the column is wider than the number.
 */
export function MoneyCell({ children }: { children: ReactNode }) {
  return <span className="block text-right tabular-nums">{children}</span>
}

/**
 * The same alignment for a figure that is not money: hours, a day count, a percentage.
 *
 * Identical to `MoneyCell` by construction and deliberately not the same component: a
 * column of hours is not a column of euros, and the day somebody adds a currency symbol
 * or a cent-rounding rule to `MoneyCell` is the day a timesheet would silently inherit
 * it. `tabular-nums` is what actually makes the digits line up -- `text-right` alone
 * lines up the last character, which is not the same thing in a proportional font.
 */
export function NumberCell({ children }: { children: ReactNode }) {
  return <span className="block text-right tabular-nums">{children}</span>
}

/**
 * Up to two initials for the chip: the first letter of each of the first two words.
 *
 * Two, not more: «Prima Società Benefit Srl» reads as `PS`, and a chip of four
 * letters is no longer a chip. Words are split on any run of whitespace so a double
 * space cannot produce an empty initial, and the result is uppercased because a
 * lowercase ragione sociale would otherwise give a chip that looks like a typo.
 */
function initialsOf(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word.charAt(0))
    .join('')
    .toUpperCase()
}

/**
 * The first column of a table when the row *is* somebody: an initials chip on Paper
 * beside the name, with an optional quiet second line (design spec §4, "prima colonna
 * con avatar/iniziali dove c'è un'entità").
 *
 * Initials rather than an image because this product stores no avatars for customers,
 * people or deals -- and the chip is `aria-hidden`: the name is right there in words,
 * so announcing "AS ACME Srl" would read the same thing twice, once as nonsense.
 *
 * A nameless record still has to render a row, so an empty name is the same em dash
 * every other empty cell shows, with no chip: an empty square beside a dash reads as a
 * broken image rather than as absence.
 */
export function EntityCell({ name, sub }: { name: string; sub?: string | null }) {
  const label = name.trim()
  if (label === '') return <span className="text-muted-foreground">{EMPTY}</span>

  return (
    <span className="flex items-center gap-3">
      <span
        data-slot="entity-initials"
        aria-hidden="true"
        className="flex size-8 shrink-0 items-center justify-center bg-[var(--color-paper)] text-xs font-medium text-foreground"
      >
        {initialsOf(label)}
      </span>
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="truncate font-medium">{label}</span>
        {sub !== null && sub !== undefined && sub !== '' ? (
          <span className="truncate text-xs text-muted-foreground">{sub}</span>
        ) : null}
      </span>
    </span>
  )
}
