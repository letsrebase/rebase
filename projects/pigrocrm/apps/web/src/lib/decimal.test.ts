import { describe, expect, it } from 'vitest'
import {
  decimalStringFromScaled,
  formatItalianQuantity,
  italianiseDecimals,
  scaledFromDecimalString,
  sumDecimalStrings,
} from './decimal'

describe('scaledFromDecimalString', () => {
  it('never multiplies a fractional value', () => {
    // Checked directly in this project's own Node runtime: `Number("0.29") * 100`
    // equals 28.999999999999996, not 29. The fractional digits are read off the
    // string; only the already-integral whole part is multiplied.
    expect(scaledFromDecimalString('0.29', 2)).toBe(29)
    expect(scaledFromDecimalString('1234.56', 2)).toBe(123456)
    expect(scaledFromDecimalString('-45.50', 2)).toBe(-4550)
    expect(scaledFromDecimalString('8', 2)).toBe(800)
    expect(scaledFromDecimalString('0.5', 2)).toBe(50)
  })

  it('handles the six-place factor scale the API sends for rates', () => {
    expect(scaledFromDecimalString('33.333333', 6)).toBe(33333333)
    expect(scaledFromDecimalString('80.000000', 6)).toBe(80000000)
  })

  it('truncates beyond the requested scale rather than rounding', () => {
    // The API never sends more places than the column holds, so this is a defensive
    // shape rather than a rounding policy: rounding here would silently disagree with
    // the backend's ROUND_HALF_UP, and two rounding rules is worse than one truncation
    // that cannot fire.
    expect(scaledFromDecimalString('1.005', 2)).toBe(100)
  })
})

describe('sumDecimalStrings', () => {
  it('adds in integer units and formats once', () => {
    expect(sumDecimalStrings(['2.50', '1.25', '0.25'], 2)).toBe('4.00')
    expect(sumDecimalStrings([], 2)).toBe('0.00')
    expect(sumDecimalStrings([null, '3.00', null], 2)).toBe('3.00')
  })

  it('is exact where the naive float sum is not', () => {
    // 300 values near the top of Numeric(12,2)'s range: the integer sum is exact,
    // the float sum displays two cents high. The same demonstration
    // `features/deals/columns.test.ts` already carries for `sumValorePrevisto`.
    const values: string[] = []
    let cents = 999999999999
    for (let index = 0; index < 300; index += 1) {
      values.push(decimalStringFromScaled(cents, 2))
      cents -= 100000300
    }
    const exact = sumDecimalStrings(values, 2)
    const naive = values.reduce((sum, value) => sum + Number(value), 0)
    expect(exact).not.toBe(naive.toFixed(2))
  })
})

describe('decimalStringFromScaled', () => {
  it('round-trips', () => {
    for (const value of ['0.00', '0.07', '12.30', '-4.05', '999.99']) {
      expect(decimalStringFromScaled(scaledFromDecimalString(value, 2), 2)).toBe(value)
    }
  })
})

describe('formatItalianQuantity', () => {
  it('drops the zeros nobody reads', () => {
    expect(formatItalianQuantity('2.00')).toBe('2')
    expect(formatItalianQuantity('0.50')).toBe('0,5')
    expect(formatItalianQuantity('4.25')).toBe('4,25')
    expect(formatItalianQuantity('24.00')).toBe('24')
    expect(formatItalianQuantity('0.00')).toBe('0')
    expect(formatItalianQuantity('1000.50')).toBe('1.000,5')
    expect(formatItalianQuantity('-0.50')).toBe('-0,5')
  })
})

describe('italianiseDecimals', () => {
  it('rewrites the figures of a sentence and nothing else', () => {
    expect(italianiseDecimals('Attività ottobre 2026 - 2.00 giorno')).toBe('Attività ottobre 2026 - 2 giorno')
    expect(italianiseDecimals('Attività ottobre 2026 - 0.50 giorno')).toBe('Attività ottobre 2026 - 0,5 giorno')
    expect(italianiseDecimals('fattura 2026/1 del 29/07/2026, 3 giorni')).toBe('fattura 2026/1 del 29/07/2026, 3 giorni')
    expect(italianiseDecimals('versione 1.2.3')).toBe('versione 1.2.3')
  })
})
