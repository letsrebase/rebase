import { money } from './format'
import type { CeilingStatus } from './queries'

/** A share the server computed (0..1) as the percentage it is: never scaled here. */
export const sharePercent = new Intl.NumberFormat('it-IT', { style: 'percent', maximumFractionDigits: 2 })

/**
 * What crossing a ceiling does, in the words the regime uses. Keyed on the API's closed
 * `consequence` enum, so a third consequence makes this map fail to compile instead of
 * printing nothing. «Se la superi» before the ceiling is crossed and «Superata» after it:
 * the same sentence under a ceiling with room left would say it already happened.
 */
const Consequence: Record<CeilingStatus['consequence'], string> = {
  leaves_next_year: 'si esce dal regime forfettario dall’anno successivo.',
  leaves_immediately: 'si esce dal regime forfettario subito, nell’anno stesso.',
}

export function consequence(ceiling: CeilingStatus['consequence'], crossed: boolean): string {
  return `${crossed ? 'Superata:' : 'Se la superi,'} ${Consequence[ceiling]}`
}

/** The fill of a bar with room left is the ink the other charts use for what was collected
 *  (`--chart-2`), never the accent hue, which reads as a warning. One that has crossed its
 *  ceiling is the destructive tint; one inside an alert level is that tint mixed into the
 *  ordinary ink. */
export const FILL_TONE = 2
const FILL_CROSSED = 'var(--destructive)'
const FILL_ALERT = 'color-mix(in oklab, var(--destructive) 45%, var(--chart-2))'

export function fill(exceeded: boolean, alert: string | null): string | undefined {
  if (exceeded) return FILL_CROSSED
  return alert === null ? undefined : FILL_ALERT
}

/** «25.000,00 €» out of a signed decimal string: the sign is the sentence's job, never
 *  a minus in front of a figure that reads «superata di». */
export function magnitude(value: string): string {
  return money(value.replace(/^-/, ''))
}
