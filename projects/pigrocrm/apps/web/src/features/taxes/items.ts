import { ITEM_STATE_TONE, isZero } from '@/features/cash/schedule/labels'
import type { StatusTone } from '@/components/StatusPill'
import { daysBetweenIso } from '@/lib/dates'
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
