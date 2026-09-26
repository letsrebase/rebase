import { describe, expect, it } from 'vitest'
import { amountFilter, amountNumber, machineAmount } from './amount'

describe('machineAmount', () => {
  it('reads an amount the Italian way: a dot before three digits is a thousands separator, a comma the decimal', () => {
    expect(machineAmount('12.000')).toBe('12000')
    expect(machineAmount('1.500')).toBe('1500')
    expect(machineAmount('1.234,50')).toBe('1234.50')
    expect(machineAmount('480')).toBe('480')
    expect(machineAmount('480,50')).toBe('480.50')
    expect(machineAmount('480.50')).toBe('480.50')
    expect(machineAmount('480.5')).toBe('480.5')
    expect(machineAmount('0,5')).toBe('0.5')
    expect(machineAmount(' 12.000 ')).toBe('12000')
  })

  it('keeps the API’s own two decimals, so a saved amount shown back reads the same', () => {
    expect(machineAmount('1500.00')).toBe('1500.00')
    expect(machineAmount('450.00')).toBe('450.00')
  })

  it('leaves what is not an amount as typed, for the field to refuse', () => {
    expect(machineAmount('')).toBe('')
    expect(machineAmount('tanto')).toBe('tanto')
  })
})

describe('amountNumber', () => {
  it('is the number an amount field holds, read the Italian way', () => {
    expect(amountNumber('1.500')).toBe(1500)
    expect(amountNumber('1.234,50')).toBe(1234.5)
    expect(amountNumber('1500.00')).toBe(1500)
  })

  it('is NaN for a blank field or anything that is not an amount, never 0', () => {
    expect(amountNumber('')).toBeNaN()
    expect(amountNumber('   ')).toBeNaN()
    expect(amountNumber('tanto')).toBeNaN()
  })
})

describe('amountFilter', () => {
  it('sends a list filter in the machine form, and leaves out one that is not an amount', () => {
    expect(amountFilter('1.500')).toBe('1500')
    expect(amountFilter('1.234,50')).toBe('1234.50')
    expect(amountFilter('0')).toBe('0')
    expect(amountFilter(undefined)).toBeUndefined()
    expect(amountFilter('tanto')).toBeUndefined()
    expect(amountFilter('-5')).toBeUndefined()
  })
})
