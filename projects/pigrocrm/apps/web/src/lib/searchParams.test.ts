import { describe, expect, it } from 'vitest'
import { booleanSearchParam } from './searchParams'

describe('booleanSearchParam', () => {
  it('accepts the boolean a <Link> sends', () => {
    // The router parses each search value with `JSON.parse`, so `search={{ scadute: true }}`
    // arrives as a real boolean rather than as a string.
    expect(booleanSearchParam(true)).toBe(true)
  })

  it('accepts the string a hand-typed URL sends', () => {
    expect(booleanSearchParam('true')).toBe(true)
  })

  it('reads "false" as no filter, which Boolean() does not', () => {
    // `Boolean("false")` is `true`. This single line is why the helper exists: it is the
    // difference between "show me every deal" and "show me the four the dashboard counted".
    expect(Boolean('false')).toBe(true)
    expect(booleanSearchParam('false')).toBeUndefined()
    expect(booleanSearchParam(false)).toBeUndefined()
  })

  it('reads anything else as no filter rather than as a third state', () => {
    for (const value of ['vero', '1', 'TRUE', '', 0, 1, null, undefined, {}, []]) {
      expect(booleanSearchParam(value)).toBeUndefined()
    }
  })

  it('answers undefined and never false, so the key leaves both the URL and the query', () => {
    // `false` would be serialised -- into the URL TanStack rebuilds, and into the query
    // object `openapi-fetch` sends -- and an API that reads the presence of the key would
    // then filter on a request that asked for no filter.
    expect(Object.hasOwn({ overdue: booleanSearchParam('nope') }, 'overdue')).toBe(true)
    expect(JSON.stringify({ overdue: booleanSearchParam('nope') })).toBe('{}')
  })
})
