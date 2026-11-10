import { describe, expect, it } from 'vitest'
import { dateLabel, distanceLabel, groupByDueDate, nextDays, taxOfDate } from './dates'
import type { TaxCalendarEntry } from './labels'

const entry = (overrides: Partial<TaxCalendarEntry>): TaxCalendarEntry => ({
  nominal_date: '2026-06-30',
  due_date: '2026-06-30',
  tax: 'income_tax',
  kind: 'balance_and_first_advance',
  cadence: null,
  covers: 'the balance of the year before and the first advance of the year',
  source: 'Agenzia delle Entrate',
  ...overrides,
})

describe('dateLabel', () => {
  it('names the tax year each income tax payment is for', () => {
    expect(dateLabel(entry({}))).toBe('Saldo 2025 e primo acconto 2026')
    expect(dateLabel(entry({ nominal_date: '2026-11-30', kind: 'second_advance' }))).toBe(
      'Secondo acconto 2026',
    )
    expect(dateLabel(entry({ nominal_date: '2026-12-16', kind: 'instalments_end' }))).toBe(
      'Ultima rata di saldo 2025 e primo acconto 2026',
    )
  })

  it('names the month a monthly IVA payment settles, which is the month before', () => {
    const monthly = entry({ tax: 'iva', kind: 'periodic_payment', cadence: 'monthly' })
    expect(dateLabel({ ...monthly, nominal_date: '2026-03-16' })).toBe('IVA di febbraio')
    expect(dateLabel({ ...monthly, nominal_date: '2026-08-20' })).toBe('IVA di luglio')
    expect(dateLabel({ ...monthly, nominal_date: '2026-01-16' })).toBe('IVA di dicembre')
  })

  it('says the other IVA dates by their kind', () => {
    const iva = entry({ tax: 'iva', cadence: 'quarterly' })
    expect(dateLabel({ ...iva, kind: 'annual_balance', nominal_date: '2026-03-16' })).toBe(
      'Saldo IVA 2025',
    )
    expect(dateLabel({ ...iva, kind: 'quarterly_payment', nominal_date: '2026-05-16' })).toBe(
      'IVA trimestrale',
    )
    expect(dateLabel({ ...iva, kind: 'advance', cadence: null, nominal_date: '2026-12-27' })).toBe(
      'Acconto IVA 2026',
    )
  })

  it('names the tax a date belongs to', () => {
    expect(taxOfDate(entry({ tax: 'substitute_tax' }))).toBe('Imposta sostitutiva')
    expect(taxOfDate(entry({ tax: 'iva' }))).toBe('IVA')
  })
})

describe('the days', () => {
  const a = entry({})
  const b = entry({ tax: 'substitute_tax' })
  const c = entry({ nominal_date: '2026-11-30', due_date: '2026-11-30', kind: 'second_advance' })
  const d = entry({ nominal_date: '2026-12-16', due_date: '2026-12-16', kind: 'instalments_end' })

  it('groups the lines of one day, in time order', () => {
    expect(groupByDueDate([c, a, b]).map((day) => [day.date, day.entries.length])).toEqual([
      ['2026-06-30', 2],
      ['2026-11-30', 1],
    ])
  })

  it('takes the next days from today, today included, never the past', () => {
    expect(nextDays([a, b, c, d], '2026-11-30', 2).map((day) => day.date)).toEqual([
      '2026-11-30',
      '2026-12-16',
    ])
    expect(nextDays([a, b, c, d], '2026-12-17', 2)).toEqual([])
  })

  it('uses the day a weekend or holiday moved the deadline to', () => {
    const moved = entry({ nominal_date: '2026-05-16', due_date: '2026-05-18' })
    expect(nextDays([moved], '2026-05-17', 2).map((day) => day.date)).toEqual(['2026-05-18'])
  })
})

describe('distanceLabel', () => {
  it('is a difference of two dates in words', () => {
    expect(distanceLabel('2026-11-10', '2026-11-30')).toBe('tra 20 giorni')
    expect(distanceLabel('2026-11-10', '2026-11-10')).toBe('oggi')
    expect(distanceLabel('2026-11-10', '2026-11-11')).toBe('domani')
    expect(distanceLabel('2026-11-10', '2026-11-09')).toBe('ieri')
    expect(distanceLabel('2026-11-10', '2026-11-07')).toBe('3 giorni fa')
  })
})
