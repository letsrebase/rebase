import { describe, expect, it } from 'vitest'
import CASES from '../../../../../../shared/format-cases/format-cases.json'
import { formatRate, rateWithArticle, referralExample, roundedShare } from './referral'

// The Italian rate, article and rounding rules live in one fixture the website's tests load
// too (`projects/website/src/referral.js` writes them the same way).
describe('formatRate: a rate as the member reads it', () => {
  it.each(CASES.formatRate)('$rate is $text', ({ rate, text }) => {
    expect(formatRate(rate)).toBe(text)
  })

  it.each([...CASES.unreadableRates, undefined])('says nothing for %s', (rate) => {
    expect(formatRate(rate)).toBeNull()
  })
})

describe('rateWithArticle: the article the Italian reading of a live rate takes', () => {
  it.each(CASES.rateWithArticle)('$rate reads $text', ({ rate, text }) => {
    expect(rateWithArticle(rate)).toBe(text)
  })

  it('has nothing to say for a rate that is not one', () => {
    expect(rateWithArticle('boh')).toBeNull()
    expect(rateWithArticle(null)).toBeNull()
  })
})

describe('referralExample: the invented engagement of the popover', () => {
  // The fixture speaks whole cents; this one returns the decimal strings the API does.
  const euros = (cents: number | null) => (cents === null ? null : (cents / 100).toFixed(2))

  it.each(CASES.referralExample)(
    'works $rate_freelancer and $rate_company over the example margin',
    ({ rate_freelancer, rate_company, margin, freelancer, company }) => {
      const example = referralExample({ rate_freelancer, rate_company })
      expect(example.margin).toBe(euros(margin))
      expect(example.freelancer).toBe(euros(freelancer))
      expect(example.company).toBe(euros(company))
    },
  )
})

describe('roundedShare: to the cent, half to even, like the backend quantize', () => {
  // Python: Decimal('0.05') * Decimal('0.5000') -> quantize(0.01) is 0.02 (2.5 cents),
  // 0.07 * 0.5 is 0.04 (3.5 cents), 0.01 * 0.5 is 0.00 (0.5 cent), 0.03 * 0.5 is 0.02.
  it.each(CASES.roundedShare)('$cents cents at $units ten-thousandths is $expected cents', ({ cents, units, expected }) => {
    expect(roundedShare(cents, units)).toBe(expected)
  })

  it.each([
    [7, 4999, 3],
    [7, 5001, 4],
    [200000, 1250, 25000],
  ])('%i cents at %i ten-thousandths is %i cents', (cents, units, expected) => {
    expect(roundedShare(cents, units)).toBe(expected)
  })
})
