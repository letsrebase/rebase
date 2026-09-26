import { defaultParseSearch } from '@tanstack/react-router'
import { describe, expect, it } from 'vitest'
import { parseSearch } from './search'

describe('parseSearch keeps an amount as it was typed in the address (REB-531)', () => {
  it.each(['tariffa_min', 'tariffa_max', 'budget_min', 'budget_max'])(
    'keeps a hand-typed «1.500» in %s as text, not the number 1.5',
    (param) => {
      expect(parseSearch(`?${param}=1.500`)).toEqual({ [param]: '1.500' })
    },
  )

  it('keeps the other Italian forms as text too', () => {
    expect(parseSearch('?tariffa_min=1.234%2C50&budget_max=50')).toEqual({
      tariffa_min: '1.234,50',
      budget_max: '50',
    })
  })

  it('reads the quoted string the app writes the same way the default does', () => {
    expect(parseSearch('?tariffa_min=%221.500%22')).toEqual({ tariffa_min: '1.500' })
  })

  it('leaves every other parameter as the default reads it', () => {
    const search = '?stato=nuovo&has_cv=true&tariffa_max=1.500&n=3&q=%22ciao%22'
    const { tariffa_max, ...rest } = parseSearch(search)
    const expected: Record<string, unknown> = { ...defaultParseSearch(search) }
    delete expected.tariffa_max
    expect(tariffa_max).toBe('1.500')
    expect(rest).toEqual(expected)
    expect(rest).toMatchObject({ has_cv: true, n: 3, q: 'ciao' })
  })

  it('reads an address with no amount exactly as the default does', () => {
    expect(parseSearch('')).toEqual(defaultParseSearch(''))
    expect(parseSearch('?q=rossi')).toEqual(defaultParseSearch('?q=rossi'))
  })
})
