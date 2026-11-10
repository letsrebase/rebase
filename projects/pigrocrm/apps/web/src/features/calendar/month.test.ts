import { describe, expect, it } from 'vitest'
import { firstOfMonth, monthCells, monthLabel, shiftMonth } from './month'

/**
 * The month's arithmetic, proven with no React and no network -- the same discipline
 * `features/time/week.test.ts` applies to the week, and for the same reason: every
 * calendar bug this project has had was a date built or read in the wrong timezone.
 */
describe('shiftMonth', () => {
  it('moves one month either way', () => {
    expect(shiftMonth('2026-09', -1)).toBe('2026-08')
    expect(shiftMonth('2026-09', 1)).toBe('2026-10')
  })

  it('crosses the year in both directions', () => {
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
  })

  it('never skips a month, whatever day the anchor was', () => {
    // The whole reason this is a function: `new Date(2026, 2, 31).setMonth(1)` asks for
    // 31 February and JavaScript answers 3 March, so «previous month» from a 31st would
    // silently land back in March. Anchoring to the first makes every shift exact.
    expect(shiftMonth('2026-03', -1)).toBe('2026-02')
    expect(shiftMonth('2026-05', -1)).toBe('2026-04')
    expect(shiftMonth('2026-07', -1)).toBe('2026-06')
  })
})

describe('firstOfMonth', () => {
  it('is local midnight of the first, not UTC midnight', () => {
    // `new Date("2026-09-01")` is UTC midnight and renders as 31 August anywhere behind
    // UTC. Built from parts, the calendar day survives wherever this runs.
    const first = firstOfMonth('2026-09')
    expect([first.getFullYear(), first.getMonth(), first.getDate()]).toEqual([2026, 8, 1])
    expect(first.getHours()).toBe(0)
  })
})

describe('monthCells', () => {
  it('starts on the Monday of the week the month starts in', () => {
    // September 2026 starts on a Tuesday, so the grid opens with Monday 31 August.
    const cells = monthCells('2026-09')
    expect(cells[0]).toEqual({ iso: '2026-08-31', day: 31, inMonth: false })
    expect(cells[1]).toEqual({ iso: '2026-09-01', day: 1, inMonth: true })
  })

  it('is always whole weeks, so the grid is a rectangle', () => {
    for (const month of ['2026-01', '2026-02', '2026-09', '2026-11', '2024-02']) {
      expect(monthCells(month).length % 7).toBe(0)
    }
  })

  it('contains every day of the month exactly once', () => {
    const inMonth = monthCells('2026-09').filter((cell) => cell.inMonth)
    expect(inMonth).toHaveLength(30)
    expect(new Set(inMonth.map((cell) => cell.iso)).size).toBe(30)
    expect(inMonth[0]?.iso).toBe('2026-09-01')
    expect(inMonth.at(-1)?.iso).toBe('2026-09-30')
  })

  it('handles a February that starts on a Sunday, and a leap one', () => {
    // February 2026 starts on a Sunday: six leading days, and the month fits in five
    // rows. The leap day is the case a hand-rolled length would get wrong.
    expect(monthCells('2026-02')[0]?.iso).toBe('2026-01-26')
    const leap = monthCells('2024-02').filter((cell) => cell.inMonth)
    expect(leap).toHaveLength(29)
    expect(leap.at(-1)?.iso).toBe('2024-02-29')
  })

  it('stops after the week that ends the month, without an empty sixth row', () => {
    // A five-week month drawn in six rows reads as a month with a week nobody works.
    const cells = monthCells('2026-02')
    expect(cells.length).toBe(35)
    expect(cells.at(-1)?.inMonth).toBe(false)
    // And the trailing days are real days, not blanks: clicking 1 March from February's
    // last row has to open 1 March.
    expect(cells.at(-1)?.iso).toBe('2026-03-01')
  })

  it('does not lose a day across a DST change', () => {
    // Italy moves to summer time on the last Sunday of March: 29 March 2026 has 23
    // hours. A grid built by adding 24 hours at a time would land on 30 March twice and
    // skip a cell -- adding one *day* through `setDate` does not.
    const march = monthCells('2026-03').filter((cell) => cell.inMonth)
    expect(march.map((cell) => cell.iso)).toContain('2026-03-29')
    expect(march).toHaveLength(31)
  })
})

describe('monthLabel', () => {
  it('reads as a month a person would say', () => {
    expect(monthLabel('2026-09')).toBe('settembre 2026')
  })
})
