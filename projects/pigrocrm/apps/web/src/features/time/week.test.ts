import { describe, expect, it } from 'vitest'
import { toIsoDate } from '@/lib/dates'
import {
  buildGrid,
  columnTotal,
  gridTotal,
  hoursForInput,
  normaliseHours,
  rowTotal,
  shiftWeek,
  weekDays,
} from './week'

const DEAL_A = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'
const DEAL_B = 'bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb'

const entry = (dealId: string, data: string, hours: string, id = `${dealId}-${data}`) =>
  ({ id, deal_id: dealId, data, hours }) as never

describe('weekDays', () => {
  it('is Monday-first and seven days long', () => {
    // Italian working weeks start on Monday; ICU's `it-IT` agrees, but the grid must
    // not depend on a locale lookup for its column order.
    const days = weekDays(new Date(2026, 2, 12)) // Thursday 12 March 2026
    expect(days).toHaveLength(7)
    expect(days[0]?.iso).toBe('2026-03-09')
    expect(days[6]?.iso).toBe('2026-03-15')
    expect(days[0]?.short).toBe('lun')
    expect(days[0]?.label).toBe('lunedì 9 marzo')
  })

  it('crosses a month and a year boundary without shifting a day', () => {
    // `new Date("2026-01-01")` is UTC midnight, which is 31 December in any zone behind
    // UTC. Everything here is built from local parts, so the boundary is stable.
    expect(weekDays(new Date(2026, 0, 1))[0]?.iso).toBe('2025-12-29')
    expect(weekDays(new Date(2026, 11, 31))[6]?.iso).toBe('2027-01-03')
  })
})

describe('shiftWeek', () => {
  it('moves whole weeks in both directions', () => {
    expect(toIsoDate(shiftWeek(new Date(2026, 2, 12), -1))).toBe('2026-03-05')
    expect(toIsoDate(shiftWeek(new Date(2026, 2, 12), 1))).toBe('2026-03-19')
  })

  it('carries a late-evening anchor across a DST boundary without losing the day', () => {
    // Italy moves to CEST on Sunday 29 March 2026. A `Date` shifted by adding
    // 7 * 86_400_000 milliseconds lands an hour early and, from 00:30, on the previous
    // calendar day; rebuilding from year/month/day parts cannot.
    expect(toIsoDate(shiftWeek(new Date(2026, 2, 25, 0, 30), 1))).toBe('2026-04-01')
  })
})

describe('the grid and its totals', () => {
  const days = weekDays(new Date(2026, 2, 12))
  const grid = buildGrid(
    [
      entry(DEAL_A, '2026-03-09', '2.50'),
      entry(DEAL_A, '2026-03-11', '1.25'),
      entry(DEAL_B, '2026-03-09', '4.00'),
      entry(DEAL_A, '2026-03-30', '8.00'), // outside the week
    ],
    days,
  )

  it('places each entry in its deal row and its day column', () => {
    expect(grid.get(DEAL_A)?.get('2026-03-09')?.hours).toBe('2.50')
    expect(grid.get(DEAL_A)?.get('2026-03-10')?.hours).toBeNull()
    expect(grid.get(DEAL_A)?.get('2026-03-30')).toBeUndefined()
  })

  it('sums rows, columns and the whole grid in integer hundredths', () => {
    // The only arithmetic this slice permits in the browser, and only for hours (§6).
    const rowA = grid.get(DEAL_A)
    expect(rowA && rowTotal(rowA)).toBe('3.75')
    expect(columnTotal(grid, '2026-03-09')).toBe('6.50')
    expect(gridTotal(grid)).toBe('7.75')
  })

  it('treats a day with no entry as contributing nothing, not zero hours logged', () => {
    expect(columnTotal(grid, '2026-03-15')).toBe('0.00')
  })

  it('opens no row for a deal whose only entries fall outside the week', () => {
    // The row set is what the week actually contains; anything else would be the
    // caller's job (the grid's own list of deals), not this function's.
    const other = buildGrid([entry(DEAL_B, '2026-03-30', '8.00')], days)
    expect(other.size).toBe(0)
  })

  it('keeps the first of several entries on the same deal and day', () => {
    // `time_entries` allows more than one entry per deal per day (§4.1) and the deal's
    // own Ore tab lists them all; a single cell cannot represent three of them, so it
    // shows the first rather than pretending the later ones overwrote it.
    const busy = buildGrid(
      [
        entry(DEAL_A, '2026-03-09', '2.50', 'first'),
        entry(DEAL_A, '2026-03-09', '1.00', 'second'),
      ],
      days,
    )
    expect(busy.get(DEAL_A)?.get('2026-03-09')).toEqual({ entryId: 'first', hours: '2.50' })
  })
})

describe('normaliseHours and hoursForInput', () => {
  it('translates the comma an Italian keyboard produces into the dot the API parses', () => {
    expect(normaliseHours(' 3,5 ')).toBe('3.5')
    expect(normaliseHours('3.5')).toBe('3.5')
    expect(normaliseHours('  ')).toBe('')
  })

  it('reads a stored value back in the shape it was typed', () => {
    expect(hoursForInput('2.50')).toBe('2,50')
    expect(hoursForInput(null)).toBe('')
  })
})
