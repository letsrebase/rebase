import { describe, expect, it } from 'vitest'
import { machineAmount } from './amount'

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
