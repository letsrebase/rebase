import { describe, expect, it } from 'vitest'
// The table the core's `test_amounts.py` runs too (REB-485), so the web and the API cannot
// read an amount two ways. `amount` is `null` for a refusal.
import CASES from '../../../../packages/core/tests/amount_cases.json'
import { acceptedAmount, amountFilter, amountNumber, euroAmount, machineAmount } from './amount'

describe('amountNumber', () => {
  it.each(CASES)('reads «$typed» as $amount', ({ typed, amount }) => {
    if (amount === null) expect(amountNumber(typed)).toBeNaN()
    else expect(amountNumber(typed)).toBe(amount)
  })
})

describe('machineAmount', () => {
  it('writes an amount in the machine form the API takes', () => {
    expect(machineAmount('1.500')).toBe('1500')
    expect(machineAmount('1.234,50')).toBe('1234.50')
    expect(machineAmount(' 12.000 ')).toBe('12000')
  })

  it('keeps the API’s own two decimals, so a saved amount shown back reads the same', () => {
    expect(machineAmount('1500.00')).toBe('1500.00')
    expect(machineAmount('450.00')).toBe('450.00')
  })

  it('leaves what is not an amount as typed, for the field to refuse', () => {
    expect(machineAmount('')).toBe('')
    expect(machineAmount(' tanto ')).toBe('tanto')
    expect(machineAmount('1,000.00')).toBe('1,000.00')
  })
})

describe('euroAmount', () => {
  it('is an amount with at most the two decimals the API keeps', () => {
    expect(euroAmount('1.234,50')).toBe(1234.5)
    expect(euroAmount('1500.00')).toBe(1500)
    expect(euroAmount('12,345')).toBeNaN()
    expect(euroAmount('12.3456')).toBeNaN()
    expect(euroAmount('0x10')).toBeNaN()
  })
})

describe('acceptedAmount', () => {
  it('is a day rate or a daily budget the API takes, from 1 to 99999.99', () => {
    expect(acceptedAmount('1.500')).toBe(true)
    expect(acceptedAmount('99.999,99')).toBe(true)
    expect(acceptedAmount('0,50')).toBe(false)
    expect(acceptedAmount('100.000')).toBe(false)
    expect(acceptedAmount('12,345')).toBe(false)
  })
})

describe('amountFilter', () => {
  it('sends a list filter in the machine form, and leaves out one that is not an amount', () => {
    expect(amountFilter('1.500')).toBe('1500')
    expect(amountFilter('1.234,50')).toBe('1234.50')
    expect(amountFilter('0')).toBe('0')
    expect(amountFilter(undefined)).toBeUndefined()
    expect(amountFilter('tanto')).toBeUndefined()
    expect(amountFilter('0x10')).toBeUndefined()
    expect(amountFilter('12,345')).toBeUndefined()
  })
})
