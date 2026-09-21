import { describe, expect, it } from 'vitest'

import { redirectToBase } from './devBaseRedirect'

describe('redirectToBase', () => {
  it('sends the bare base (no trailing slash) to the base, keeping the query', () => {
    expect(redirectToBase('/app', '/app/')).toBe('/app/')
    expect(redirectToBase('/app?tab=commerciale&da=2026-09-01', '/app/')).toBe(
      '/app/?tab=commerciale&da=2026-09-01',
    )
  })

  it('leaves every other URL alone', () => {
    expect(redirectToBase('/app/', '/app/')).toBeNull()
    expect(redirectToBase('/app/customers', '/app/')).toBeNull()
    expect(redirectToBase('/application', '/app/')).toBeNull()
    expect(redirectToBase('/api/auth/me', '/app/')).toBeNull()
    expect(redirectToBase('/', '/app/')).toBeNull()
  })

  it('also catches the bare base under a space prefix', () => {
    expect(redirectToBase('/spazio-x/app?tab=economica', '/app/')).toBe('/spazio-x/app/?tab=economica')
    expect(redirectToBase('/spazio-x/app/', '/app/')).toBeNull()
  })
})
