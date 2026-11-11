import { ITEM_STATE_TONE, isZero } from '@/features/cash/schedule/labels'
import type { StatusTone } from '@/components/StatusPill'
import { daysBetweenIso } from '@/lib/dates'
import type { DueDay } from './dates'
import type { TaxDueItem } from './queries'

/**
 * The words of a tax due item on «Tasse» (the brief's «Inferred» list: «Previsto», «Da pagare»,
 * «Pagato», «Scaduto», «Non più dovuto», the same as the cash schedule's). Nothing here adds an
 * amount: a state is read off the server's own fields and a day count is a difference of dates.
 */

export type TaxItemState = 'forecast' | 'expected' | 'overdue' | 'settled' | 'dropped'

/** The state an item stands in today: an expected item past its date is «Scaduto». */
export function taxItemState(item: TaxDueItem, today: string): TaxItemState {
  if (item.state === 'expected' && item.due_date !== null && item.due_date < today) return 'overdue'
  return item.state
}

export const TAX_ITEM_STATE_LABEL: Record<TaxItemState, string> = {
  forecast: 'Previsto',
  expected: 'Da pagare',
  overdue: 'Scaduto',
  settled: 'Pagato',
  dropped: 'Non più dovuto',
}

/** Tones beside their words; `overdue` is the one `danger` and `accent` is never used. */
export const TAX_ITEM_STATE_TONE: Record<TaxItemState, StatusTone> = {
  forecast: ITEM_STATE_TONE.forecast,
  expected: ITEM_STATE_TONE.expected,
  overdue: 'danger',
  settled: ITEM_STATE_TONE.settled,
  dropped: ITEM_STATE_TONE.dropped,
}

export const CARRY_LABEL = 'Da confermare'
/** A difference waiting for a person is waiting like a payment: `gold`, never `accent`. */
export const CARRY_TONE: StatusTone = 'gold'

/** «da 3 giorni»: how long an item has been overdue, a difference of two dates. */
export function overdueSince(item: TaxDueItem, today: string): string | null {
  if (item.due_date === null) return null
  const days = daysBetweenIso(item.due_date, today)
  if (days === null || days <= 0) return null
  return days === 1 ? 'da 1 giorno' : `da ${days} giorni`
}

/** A payment partly made: `settled` is not zero and something remains (the server's strings). */
export function partlyPaid(item: TaxDueItem): boolean {
  return item.state === 'expected' && !isZero(item.settled)
}

/** What an item says in a list: its own words up to the sentence about a carried difference,
 *  which the row carries as its own «Da confermare» mark and the panel in full. */
export function itemTitle(item: TaxDueItem): string {
  const text = item.description ?? ''
  const cut = text.indexOf('. Include ')
  return cut === -1 ? text : text.slice(0, cut)
}

export type F24Chip = 'todo' | 'created' | 'paid' | 'paid_receipt'

export const F24_CHIP_LABEL: Record<F24Chip, string> = {
  todo: 'F24 da creare',
  created: 'F24 creato',
  paid: 'F24 pagato',
  paid_receipt: 'F24 pagato · ricevuta',
}

export const F24_CHIP_TONE: Record<F24Chip, StatusTone> = {
  todo: 'gold',
  created: 'muted',
  paid: 'ink',
  paid_receipt: 'ink',
}

/** The items of a day an F24 can still take: open, owed to the tax office, with nothing paid on
 *  them (the server refuses the rest and says why). A credit is among them. */
export function groupableItems(day: DueDay): TaxDueItem[] {
  const grouped = new Set(day.groups.flatMap((group) => group.lines.map((line) => line.due_item_id)))
  return day.items.filter(
    (item) =>
      (item.state === 'forecast' || item.state === 'expected') &&
      isZero(item.settled) &&
      !grouped.has(item.id),
  )
}

/** The chip on a date's row (brief, question 3): «da creare» when items wait for a document,
 *  «creato» for an open group, «pagato» for a paid one, with «ricevuta» when a receipt is attached. */
export function f24Chip(day: DueDay): F24Chip | null {
  const group = day.groups.find((candidate) => candidate.state !== 'dissolved')
  if (group) {
    if (group.state === 'open') return 'created'
    return group.receipts.length > 0 ? 'paid_receipt' : 'paid'
  }
  return groupableItems(day).length > 0 ? 'todo' : null
}
