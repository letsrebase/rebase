/** REB-273: the token must be out of the URL before anything else can see it. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stripEntraToken, takeEntraToken } from './entra-token'

const HUB = /^\/hub\/verify$/
const CRM = /\/app\/(verify|invite)$/

/** The module reads `window.location` and rewrites it with `history.replaceState`:
 *  a URL and those two, which is all this (node) environment needs to stand in. */
function setLocation(path: string) {
  const url = new URL(path, 'https://letsrebase.com')
  vi.stubGlobal('window', {
    location: {
      href: url.href,
      pathname: url.pathname,
      get search() {
        return url.search
      },
      get hash() {
        return url.hash
      },
    },
    history: {
      state: null,
      replaceState: (_state: unknown, _title: string, next: string) => {
        const target = new URL(next, url)
        url.search = target.search
        url.hash = target.hash
      },
    },
  })
}

beforeEach(() => setLocation('/'))

afterEach(() => {
  takeEntraToken()
  vi.unstubAllGlobals()
})

describe('stripEntraToken', () => {
  it('takes the token out of the URL', () => {
    setLocation('/hub/verify?t=abc123')
    stripEntraToken(HUB)
    expect(window.location.search).toBe('')
    expect(takeEntraToken()).toBe('abc123')
  })

  it('keeps other params and the hash, and only removes t', () => {
    setLocation('/hub/verify?t=abc123&x=1#top')
    stripEntraToken(HUB)
    expect(window.location.search).toBe('?x=1')
    expect(window.location.hash).toBe('#top')
    expect(takeEntraToken()).toBe('abc123')
  })

  it('is consumed once: a second read finds nothing', () => {
    setLocation('/hub/verify?t=abc123')
    stripEntraToken(HUB)
    expect(takeEntraToken()).toBe('abc123')
    expect(takeEntraToken()).toBeNull()
  })

  it('does nothing off the given paths, token left in the URL', () => {
    setLocation('/hub/login?t=abc123')
    stripEntraToken(HUB)
    expect(window.location.search).toBe('?t=abc123')
    expect(takeEntraToken()).toBeNull()
  })

  it('does nothing on a matching path with no token', () => {
    setLocation('/hub/verify')
    stripEntraToken(HUB)
        expect(takeEntraToken()).toBeNull()
  })

  it.each(['/app/verify', '/app/invite', '/s/studio/app/invite'])('matches the CRM path %s', (path) => {
    setLocation(`${path}?t=abc123`)
    stripEntraToken(CRM)
    expect(window.location.search).toBe('')
    expect(takeEntraToken()).toBe('abc123')
  })
})
