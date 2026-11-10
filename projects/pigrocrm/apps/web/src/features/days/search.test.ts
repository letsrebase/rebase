import { describe, expect, it } from 'vitest'
import { defaultMonth, monthWindow, validateDaysSearch, validateNewDaySearch } from './search'

const ID = '5b2c70c2-0000-4000-8000-000000000001'

describe('the days register’s URL', () => {
  it('keeps a contract and a month and drops anything malformed', () => {
    expect(validateDaysSearch({ contract: ID, month: '2026-03' })).toEqual({ contract: ID, month: '2026-03' })
    expect(validateDaysSearch({ contract: 'not-an-id', month: '2026-13' })).toEqual({
      contract: undefined,
      month: undefined,
    })
    expect(validateDaysSearch({ contract: 42, month: null })).toEqual({ contract: undefined, month: undefined })
  })

  it('keeps a contract and a date for the form and drops a date that is not one', () => {
    expect(validateNewDaySearch({ contract: ID, date: '2026-03-02' })).toEqual({ contract: ID, date: '2026-03-02' })
    expect(validateNewDaySearch({ date: '2026-02-30x' })).toEqual({ contract: undefined, date: undefined })
  })
})

describe('monthWindow', () => {
  it('is the first and last day of the month, February and its leap year included', () => {
    expect(monthWindow('2026-03')).toEqual({ from: '2026-03-01', to: '2026-03-31' })
    expect(monthWindow('2026-02')).toEqual({ from: '2026-02-01', to: '2026-02-28' })
    expect(monthWindow('2028-02')).toEqual({ from: '2028-02-01', to: '2028-02-29' })
    expect(monthWindow('2026-12')).toEqual({ from: '2026-12-01', to: '2026-12-31' })
  })

  it('opens on the month it is given', () => {
    expect(defaultMonth(new Date(2026, 9, 26))).toBe('2026-10')
  })
})
