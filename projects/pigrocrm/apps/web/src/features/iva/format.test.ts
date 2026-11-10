import { describe, expect, it } from 'vitest'
import { formatPercent, groupsLabel, rateLabel } from './format'

describe('the words of a rate', () => {
  it('trims the zeros of a percentage by its string and keeps a real fraction', () => {
    expect(formatPercent('22.00')).toBe('22 %')
    expect(formatPercent('10.00')).toBe('10 %')
    expect(formatPercent('5.50')).toBe('5,5 %')
    expect(formatPercent('0.00')).toBe('0 %')
    expect(formatPercent('100.00')).toBe('100 %')
  })

  it('names a zero rate by its nature', () => {
    expect(rateLabel('0.00', 'N2.1')).toBe('N2.1')
    expect(rateLabel('22.00', null)).toBe('22 %')
  })

  it('says «2 aliquote» for a mixed document and its one rate otherwise', () => {
    const g = (rate: string, nature: string | null = null) => ({ rate, nature })
    expect(groupsLabel([g('22.00')])).toBe('22 %')
    expect(groupsLabel([g('22.00'), g('0.00', 'N2.1')])).toBe('2 aliquote')
    expect(groupsLabel([g('22.00'), g('10.00'), g('4.00')])).toBe('3 aliquote')
    expect(groupsLabel([])).toBe('—')
  })
})
