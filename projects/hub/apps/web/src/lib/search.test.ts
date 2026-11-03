import { defaultParseSearch } from '@tanstack/react-router'
import { describe, expect, it } from 'vitest'
import { parseSearch } from './search'

describe('parseSearch keeps an amount as it was typed in the address (REB-531)', () => {
  it.each(['min_rate', 'max_rate', 'budget_min', 'budget_max'])(
    'keeps a hand-typed «1.500» in %s as text, not the number 1.5',
    (param) => {
      expect(parseSearch(`?${param}=1.500`)).toEqual({ [param]: '1.500' })
    },
  )

  it('keeps the other Italian forms as text too', () => {
    expect(parseSearch('?min_rate=1.234%2C50&budget_max=50')).toEqual({
      min_rate: '1.234,50',
      budget_max: '50',
    })
  })

  it('reads the quoted string the app writes the same way the default does', () => {
    expect(parseSearch('?min_rate=%221.500%22')).toEqual({ min_rate: '1.500' })
  })

  it('leaves a quoted amount with spaces before it as the default reads it', () => {
    expect(parseSearch('?min_rate=%20%221.500%22')).toEqual(defaultParseSearch('?min_rate=%20%221.500%22'))
  })

  it('leaves a repeated amount parameter as the default reads it, not its first value', () => {
    const search = '?min_rate=1.500&min_rate=50'
    expect(parseSearch(search)).toEqual(defaultParseSearch(search))
    expect(Array.isArray(parseSearch(search).min_rate)).toBe(true)
  })

  it('leaves every other parameter as the default reads it', () => {
    const search = '?stato=nuovo&has_cv=true&max_rate=1.500&n=3&q=%22ciao%22'
    const { max_rate, ...rest } = parseSearch(search)
    const expected: Record<string, unknown> = { ...defaultParseSearch(search) }
    delete expected.max_rate
    expect(max_rate).toBe('1.500')
    expect(rest).toEqual(expected)
    expect(rest).toMatchObject({ has_cv: true, n: 3, q: 'ciao' })
  })

  it.each([
    '{"progetto": "una dashboard per i clienti, React davanti e Python dietro"}',
    '[1, 2, 3] gestionali da portare in un data warehouse, quattro mesi',
    '"Siamo una fintech e ci serve una web app per i nostri clienti"',
    '12345678901234567890123456789012345678901234567890',
    'true',
  ])('keeps a project description as the words it is, whatever it reads as (REB-675): %s', (text) => {
    expect(parseSearch(`?description=${encodeURIComponent(text)}`)).toEqual({ description: text })
    expect(parseSearch(`?description=${encodeURIComponent(text)}&from=home`)).toEqual({ description: text, from: 'home' })
  })

  it.each(['{"progetto": "una dashboard per i clienti"}', '[1, 2, 3] gestionali da portare', '12345678901234567890', 'true'])(
    'keeps the project description of a link made before the 2026-11 rename (`descrizione`) as the words it is: %s',
    (text) => {
      expect(parseSearch(`?descrizione=${encodeURIComponent(text)}`)).toEqual({ 'descrizione': text })
    },
  )

  it('leaves a repeated description as the default reads it, not its first value', () => {
    expect(parseSearch('?description=a&description=b')).toEqual(defaultParseSearch('?description=a&description=b'))
    expect(parseSearch('?descrizione=a&descrizione=b')).toEqual(defaultParseSearch('?descrizione=a&descrizione=b'))
  })

  it('reads an address with no amount exactly as the default does', () => {
    expect(parseSearch('')).toEqual(defaultParseSearch(''))
    expect(parseSearch('?q=rossi')).toEqual(defaultParseSearch('?q=rossi'))
  })
})
