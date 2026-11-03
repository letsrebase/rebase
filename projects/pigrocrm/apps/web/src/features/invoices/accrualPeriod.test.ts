import { describe, expect, it } from 'vitest'
import { accrualPeriodBody, validateAccrualPeriod } from './accrualPeriod'

describe('validateAccrualPeriod', () => {
  it('accepts no period at all', () => {
    expect(validateAccrualPeriod({ accrual_from: '', accrual_to: '' })).toBeUndefined()
  })

  it('accepts a period whose ends are in order, a single day included', () => {
    expect(
      validateAccrualPeriod({ accrual_from: '2026-08-01', accrual_to: '2026-08-31' }),
    ).toBeUndefined()
    expect(
      validateAccrualPeriod({ accrual_from: '2026-08-01', accrual_to: '2026-08-01' }),
    ).toBeUndefined()
  })

  it('refuses a lone end, in either position', () => {
    const message = 'Il periodo di competenza richiede sia l’inizio sia la fine.'
    expect(validateAccrualPeriod({ accrual_from: '2026-08-01', accrual_to: '' })).toBe(message)
    expect(validateAccrualPeriod({ accrual_from: '', accrual_to: '2026-08-31' })).toBe(message)
  })

  it('refuses an end before its start, across a year boundary too', () => {
    // String order is date order for ISO dates: this is the case a naive day-of-month
    // comparison would get wrong.
    expect(
      validateAccrualPeriod({ accrual_from: '2026-01-01', accrual_to: '2025-12-31' }),
    ).toBe('La fine del periodo di competenza non può precedere l’inizio.')
  })
})

describe('accrualPeriodBody', () => {
  it('sends both dates as given', () => {
    expect(accrualPeriodBody({ accrual_from: '2026-08-01', accrual_to: '2026-08-31' })).toEqual({
      accrual_from: '2026-08-01',
      accrual_to: '2026-08-31',
    })
  })

  it('sends an explicit null for a cleared pair, never an omitted key', () => {
    // On a PATCH an omitted key is "unchanged"; a cleared input is "no period".
    expect(accrualPeriodBody({ accrual_from: '', accrual_to: '' })).toEqual({
      accrual_from: null,
      accrual_to: null,
    })
  })
})
