/** REB-229: the token must be out of the URL before anything else can see it. */
import { afterEach, describe, expect, it } from 'vitest'
import { stripEntraToken, takeEntraToken } from './entra-token'

function setLocation(path: string) {
  window.history.replaceState(null, '', path)
}

afterEach(() => {
  // Whatever a test left behind, in the URL and in the module's own one-shot slot.
  setLocation('/')
  takeEntraToken()
})

describe('stripEntraToken', () => {
  it('takes the token out of the URL at the root', () => {
    setLocation('/app/verify?t=abc123')
    stripEntraToken()
    expect(window.location.search).toBe('')
    expect(takeEntraToken()).toBe('abc123')
  })

  it('takes the token out of the URL under a space prefix', () => {
    setLocation('/acme/app/verify?t=xyz789')
    stripEntraToken()
    expect(window.location.search).toBe('')
    expect(takeEntraToken()).toBe('xyz789')
  })

  it('is consumed once: a second read finds nothing', () => {
    setLocation('/app/verify?t=abc123')
    stripEntraToken()
    expect(takeEntraToken()).toBe('abc123')
    expect(takeEntraToken()).toBeNull()
  })

  it('does nothing off the entra path, token left in the URL', () => {
    setLocation('/app/customers?t=abc123')
    stripEntraToken()
    expect(window.location.search).toBe('?t=abc123')
    expect(takeEntraToken()).toBeNull()
  })

  it('does nothing on entra with no token', () => {
    setLocation('/app/verify')
    stripEntraToken()
    expect(window.location.pathname).toBe('/app/verify')
    expect(window.location.search).toBe('')
    expect(takeEntraToken()).toBeNull()
  })
})
