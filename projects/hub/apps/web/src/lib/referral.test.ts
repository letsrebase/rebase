import { describe, expect, it } from 'vitest'
import { formatRate, rateWithArticle, referralExample, roundedShare } from './referral'

describe('formatRate: a rate as the member reads it', () => {
  it.each([
    ['0.1000', '10%'],
    ['0.3000', '30%'],
    ['0.1250', '12,5%'],
    ['0.0875', '8,75%'],
    ['0.10', '10%'],
    ['1', '100%'],
    ['0', '0%'],
  ])('%s is %s', (rate, said) => {
    expect(formatRate(rate)).toBe(said)
  })

  it.each([[null], [undefined], [''], ['abc'], ['-0.1'], ['1.5']])('says nothing for %s', (rate) => {
    expect(formatRate(rate)).toBeNull()
  })
})

describe('rateWithArticle: the article the Italian reading of a live rate takes', () => {
  it.each([
    ['0.1000', 'il 10%'],
    ['0.3000', 'il 30%'],
    ['0.0800', "l'8%"],
    ['0.0850', "l'8,5%"],
    ['0.1100', "l'11%"],
    ['0.1800', 'il 18%'],
    ['0.0100', "l'1%"],
    ['0.8000', "l'80%"],
    ['0.8900', "l'89%"],
    ['0.9000', 'il 90%'],
    ['1', 'il 100%'],
    ['0.0050', 'lo 0,5%'],
  ])('%s reads %s', (rate, said) => {
    expect(rateWithArticle(rate)).toBe(said)
  })

  it('has nothing to say for a rate that is not one', () => {
    expect(rateWithArticle('boh')).toBeNull()
    expect(rateWithArticle(null)).toBeNull()
  })
})

describe('referralExample: the invented engagement of the popover', () => {
  it('works the margin at 500 - 400 for 20 days and each live rate over it', () => {
    const example = referralExample({ rate_freelancer: '0.1000', rate_company: '0.3000' })
    expect(example.margin).toBe('2000.00')
    expect(example.freelancer).toBe('200.00')
    expect(example.company).toBe('600.00')
  })

  it('follows a rate with a decimal', () => {
    const example = referralExample({ rate_freelancer: '0.1250', rate_company: '0.3500' })
    expect(example.freelancer).toBe('250.00')
    expect(example.company).toBe('700.00')
  })

  it('goes down to the cent on a rate with two decimals in the percentage', () => {
    const example = referralExample({ rate_freelancer: '0.0875', rate_company: '0.1234' })
    expect(example.freelancer).toBe('175.00')
    expect(example.company).toBe('246.80')
  })

  it('leaves a side blank when its rate is missing, without touching the other', () => {
    const example = referralExample({ rate_freelancer: null, rate_company: '0.3000' })
    expect(example.freelancer).toBeNull()
    expect(example.company).toBe('600.00')
  })
})

describe('roundedShare: to the cent, half to even, like the backend quantize', () => {
  // Python: Decimal('0.05') * Decimal('0.5000') -> quantize(0.01) is 0.02 (2.5 cents),
  // 0.07 * 0.5 is 0.04 (3.5 cents), 0.01 * 0.5 is 0.00 (0.5 cent), 0.03 * 0.5 is 0.02.
  it.each([
    [5, 5000, 2],
    [7, 5000, 4],
    [1, 5000, 0],
    [3, 5000, 2],
    [7, 4999, 3],
    [7, 5001, 4],
    [200000, 1250, 25000],
  ])('%i cents at %i ten-thousandths is %i cents', (cents, units, expected) => {
    expect(roundedShare(cents, units)).toBe(expected)
  })
})
