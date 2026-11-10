import type { ProblemDetail } from '@/lib/api'
import type { components } from '@/lib/api-types'

type LineIn = components['schemas']['DealValueLineIn']
type LineRead = components['schemas']['DealValueLineRead']

export type LineUnit = LineIn['unit']
export type LinePeriod = LineIn['delivery_period']

/**
 * One line as the form holds it: every input is text until it is sent, so what was typed is
 * never rewritten under the cursor, and money is never a number in the browser at all (the
 * three figures under the list come from the server's `value-preview`).
 */
export interface LineDraft {
  /** Client-only identity of the row, so removing one never moves another's inputs. */
  key: string
  description: string
  unit: LineUnit
  delivery_period: LinePeriod
  unit_price: string
  quantity: string
  start: string
  duration_months: string
}

let nextKey = 0
function newKey(): string {
  nextKey += 1
  return `line-${nextKey}`
}

/** What a new deal opens with: one lump sum, paid once, so a person who knows only the
 *  total types it as before. */
export function emptyLine(): LineDraft {
  return {
    key: newKey(),
    description: '',
    unit: 'flat',
    delivery_period: 'one_off',
    unit_price: '',
    quantity: '1',
    start: '',
    duration_months: '',
  }
}

export function linesToDrafts(lines: readonly LineRead[] | undefined): LineDraft[] {
  return (lines ?? []).map((line) => ({
    key: newKey(),
    description: line.description ?? '',
    unit: line.unit,
    delivery_period: line.delivery_period,
    unit_price: line.unit_price,
    quantity: line.quantity,
    start: line.start ?? '',
    duration_months: line.duration_months === null || line.duration_months === undefined
      ? ''
      : String(line.duration_months),
  }))
}

/** A line with no price typed is not a line yet: it is left out of the request, as an empty
 *  total field was before. */
export function isSendable(line: LineDraft): boolean {
  return line.unit_price.trim() !== ''
}

/** The request's lines, from the drafts that have a price. A lump sum's quantity is always 1
 *  and a lump sum paid once has no window, so neither is read from the inputs. */
export function linesToBody(drafts: readonly LineDraft[]): LineIn[] {
  return drafts.filter(isSendable).map((line) => {
    const flat = line.unit === 'flat'
    const duration = line.duration_months.trim()
    return {
      description: line.description.trim() === '' ? null : line.description.trim(),
      unit: line.unit,
      delivery_period: line.delivery_period,
      unit_price: line.unit_price.trim(),
      quantity: flat || line.quantity.trim() === '' ? '1' : line.quantity.trim(),
      start: line.start === '' ? null : line.start,
      duration_months:
        (flat && line.delivery_period === 'one_off') || duration === ''
          ? null
          : Number.parseInt(duration, 10),
    }
  })
}

/**
 * Where the server's sentence about the lines belongs. `ValidationFailed` on the lines
 * names the row («riga 2 (Canone): ...») in the position it holds among the lines that
 * were sent; the drafts without a price were not, so the index is mapped back to the draft.
 * A sentence about the whole list (the total is too large) has no row.
 */
export function lineProblem(
  problem: ProblemDetail | null | undefined,
  drafts: readonly LineDraft[],
): { key: string | null; message: string } | null {
  if (!problem || problem.code !== 'validation_failed' || problem.field !== 'value_lines') {
    return null
  }
  const message = problem.reason ?? problem.detail
  const named = /^riga (\d+)/.exec(message)
  if (named === null) return { key: null, message }
  const sent = drafts.filter(isSendable)
  const row = sent[Number(named[1]) - 1]
  return { key: row?.key ?? null, message }
}
