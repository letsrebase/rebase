import { describe, expect, it } from 'vitest'
import { defaultPeriod, periodName, periodsOf, rangeSentence } from './periods'

const NOV_2026 = new Date(2026, 10, 10)

describe('the periods an export can be made for', () => {
  it('lists the months from the current one back, the current one tagged and never the default', () => {
    const months = periodsOf('month', NOV_2026)
    expect(months[0]).toMatchObject({
      label: 'Novembre 2026 (in corso)',
      from: '2026-11-01',
      to: '2026-11-30',
      current: true,
    })
    expect(months[1]).toMatchObject({ label: 'Ottobre 2026', from: '2026-10-01', to: '2026-10-31' })
    expect(defaultPeriod(months)?.label).toBe('Ottobre 2026')
  })

  it('lists the quarters across a year end, with their own first and last day', () => {
    const quarters = periodsOf('quarter', new Date(2026, 0, 15))
    expect(quarters.slice(0, 3).map((q) => [q.label, q.from, q.to])).toEqual([
      ['1° trimestre 2026 (in corso)', '2026-01-01', '2026-03-31'],
      ['4° trimestre 2025', '2025-10-01', '2025-12-31'],
      ['3° trimestre 2025', '2025-07-01', '2025-09-30'],
    ])
    expect(defaultPeriod(periodsOf('quarter', NOV_2026))?.label).toBe('3° trimestre 2026')
  })

  it('lists the calendar years and defaults to the last one that is over', () => {
    const years = periodsOf('year', NOV_2026)
    expect(years[0]).toMatchObject({ label: '2026 (in corso)', from: '2026-01-01', to: '2026-12-31' })
    expect(defaultPeriod(years)).toMatchObject({ from: '2025-01-01', to: '2025-12-31' })
  })

  it('keeps February of a leap year to its 29 days and no period past a year', () => {
    const feb = periodsOf('month', new Date(2028, 2, 5)).find((m) => m.value === 'month:2028-02')
    expect(feb?.to).toBe('2028-02-29')
    for (const kind of ['month', 'quarter', 'year'] as const) {
      for (const option of periodsOf(kind, NOV_2026)) {
        const days = (Date.parse(option.to) - Date.parse(option.from)) / 86_400_000
        expect(days).toBeLessThan(366)
      }
    }
  })

  it('writes the range beside the picker and names a stored period by what it is', () => {
    expect(rangeSentence('2026-07-01', '2026-09-30')).toBe('dal 01/07/2026 al 30/09/2026')
    expect(periodName('2026-07-01', '2026-09-30')).toBe('3° trimestre 2026')
    expect(periodName('2026-10-01', '2026-10-31')).toBe('Ottobre 2026')
    expect(periodName('2026-01-01', '2026-12-31')).toBe('2026')
    expect(periodName('2026-02-10', '2026-04-20')).toBe('dal 10/02/2026 al 20/04/2026')
  })
})
