import { describe, expect, it } from 'vitest'
// The table the core's `test_amounts.py` runs too (REB-485), so the web and the API cannot
// read an amount two ways. `amount` is `null` for a refusal.
import CASES from './amount_cases.json'
import { acceptedAmount, amountFilter, amountNumber, euroAmount, sentAmount } from './amount'

describe('amountNumber', () => {
  it.each(CASES)('reads «$typed» as $amount', ({ typed, amount }) => {
    if (amount === null) expect(amountNumber(typed)).toBeNaN()
    else expect(amountNumber(typed)).toBe(amount)
  })
})

describe('sentAmount', () => {
  // Every amount the table accepts with at most two decimals goes out as two decimals and a
  // dot, no grouping: the core's `test_amounts.py` reads that same form back to the same
  // amount, so no reader, the web's or the core's, can take it for another number.
  it.each(CASES.filter(({ typed, amount }) => amount !== null && Number.isFinite(euroAmount(typed))))(
    'sends «$typed» as $amount with two decimals',
    ({ typed, amount }) => {
      expect(sentAmount(typed)).toBe(amount!.toFixed(2))
    },
  )

  it('writes «1.500» as 1500.00 and «1,5» as 1.50, never «1.500» for a reader to take as thousands', () => {
    expect(sentAmount('1.500')).toBe('1500.00')
    expect(sentAmount('1,5')).toBe('1.50')
    expect(sentAmount('1,500')).toBe('1.50')
    expect(sentAmount(' 12.000 ')).toBe('12000.00')
  })

  it('leaves what is not an amount it can send as typed, for the field or the API to refuse', () => {
    expect(sentAmount('')).toBe('')
    expect(sentAmount(' tanto ')).toBe('tanto')
    expect(sentAmount('1,000.00')).toBe('1,000.00')
    expect(sentAmount('12,345')).toBe('12,345')
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
    expect(amountFilter('1.500')).toBe('1500.00')
    expect(amountFilter('1.234,50')).toBe('1234.50')
    expect(amountFilter('0')).toBe('0.00')
    expect(amountFilter(undefined)).toBeUndefined()
    expect(amountFilter('tanto')).toBeUndefined()
    expect(amountFilter('0x10')).toBeUndefined()
    expect(amountFilter('12,345')).toBeUndefined()
  })
})
