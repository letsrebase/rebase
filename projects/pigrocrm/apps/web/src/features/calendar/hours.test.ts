import { describe, expect, it } from 'vitest'
import { WorkUnit, HALF, normaliseHours } from './hours'

describe('normaliseHours', () => {
  it('takes the ordinary shapes somebody types', () => {
    expect(normaliseHours('8')).toBe('8')
    expect(normaliseHours('8.00')).toBe('8.00')
    expect(normaliseHours('0.25')).toBe('0.25')
    expect(normaliseHours(' 3.5 ')).toBe('3.5')
  })

  it('accepts a comma, because that is what an Italian keyboard makes', () => {
    // Refusing `7,5` would be refusing the number for the shape of its separator.
    expect(normaliseHours('7,5')).toBe('7.5')
  })

  it('refuses what is not hours, rather than guessing', () => {
    // `8h` and `otto` are not hours, and guessing at them is how a typo becomes a wrong
    // figure in a P&L.
    for (const value of ['', ' ', 'otto', '8h', '8 ore', '-1', 'NaN', '1e2']) {
      expect(normaliseHours(value), value).toBeNull()
    }
  })

  it('refuses zero and refuses more than a day', () => {
    // The same window `ck_time_entries_ore_range` enforces: `ore > 0 AND ore <= 24`.
    expect(normaliseHours('0')).toBeNull()
    expect(normaliseHours('0.00')).toBeNull()
    expect(normaliseHours('24')).toBe('24')
    expect(normaliseHours('24.01')).toBeNull()
    expect(normaliseHours('25')).toBeNull()
  })

  it('refuses a third decimal, which the column cannot hold', () => {
    // `time_entries.hours` is `Numeric(5, 2)`: a third decimal would be rounded by the
    // database, so the figure stored would not be the figure typed.
    expect(normaliseHours('1.005')).toBeNull()
  })

  it('has presets that are themselves valid hours', () => {
    expect(normaliseHours(WorkUnit)).toBe(WorkUnit)
    expect(normaliseHours(HALF)).toBe(HALF)
  })
})
