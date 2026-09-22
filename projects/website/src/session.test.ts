import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * session.js asks the hub's own `GET /api/hub/me` on load and, only on a real
 * answer, points the `Accedi`/«Entra nella tua area» link at the person's own area
 * (REB-349): `/hub/me` for a member, `/hub/admin` for an admin. A signed-out visitor
 * (a 401, or no `fetch` at all) leaves the link exactly where the markup put it.
 */
const source = readFileSync(join(__dirname, 'session.js'), 'utf-8')

interface Me {
  role: 'member' | 'admin'
}

interface Session {
  start: () => void
  rewrite: (root: ParentNode | undefined, to: string) => number
  landingRoute: (me: Me | null | undefined) => string
  SELECTOR: string
}

function load(): Session {
  new Function(source)()
  const api = (window as Window & { __session?: Session }).__session
  if (!api) throw new Error('session.js did not expose window.__session')
  return api
}

function fixture(): void {
  document.body.innerHTML =
    '<a class="quiet-link" href="/hub/login">Accedi</a>' +
    '<a class="quiet-link" href="/hub/login">Entra nella tua area</a>' +
    '<a class="brand" href="/">rebase</a>'
}

const hrefsOf = () => [...document.querySelectorAll('a')].map((a) => a.getAttribute('href'))

beforeEach(() => {
  fixture()
  // session.js runs `start()` the moment it loads, like consent.js: a bare `vi.fn()`
  // here would make every test that never cares about the request reject on
  // `.then` of `undefined`, so the default answer is a plain miss (no session).
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }))
})
afterEach(() => vi.unstubAllGlobals())

it('stays small', () => {
  expect(Buffer.byteLength(source, 'utf-8')).toBeLessThan(4 * 1024)
})

describe('landingRoute', () => {
  it('sends a member to /hub/me', () => {
    const { landingRoute } = load()
    expect(landingRoute({ role: 'member' })).toBe('/hub/me')
  })

  it('sends an admin to /hub/admin', () => {
    const { landingRoute } = load()
    expect(landingRoute({ role: 'admin' })).toBe('/hub/admin')
  })

  it('falls back to /hub/me for no answer at all', () => {
    const { landingRoute } = load()
    expect(landingRoute(null)).toBe('/hub/me')
    expect(landingRoute(undefined)).toBe('/hub/me')
  })
})

describe('rewrite', () => {
  it('points every /hub/login link at the given target, and only those', () => {
    const { rewrite } = load()
    const count = rewrite(document, '/hub/me')
    expect(count).toBe(2)
    expect(hrefsOf()).toEqual(['/hub/me', '/hub/me', '/'])
  })

  it('touches nothing when there is no login link', () => {
    document.body.innerHTML = '<a class="brand" href="/">rebase</a>'
    const { rewrite } = load()
    expect(rewrite(document, '/hub/me')).toBe(0)
    expect(hrefsOf()).toEqual(['/'])
  })

  it('keeps a query string utm.js already added, and only changes the path', () => {
    document.body.innerHTML =
      '<a class="quiet-link" href="/hub/login?da=home&utm_source=x">Accedi</a>'
    const { rewrite } = load()
    expect(rewrite(document, '/hub/admin')).toBe(1)
    expect(hrefsOf()).toEqual(['/hub/admin?da=home&utm_source=x'])
  })
})

describe('start (session.js runs it on load, like consent.js)', () => {
  async function flush(): Promise<void> {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  }

  it('asks the hub, credentialed and same-origin, and nothing else', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false })
    vi.stubGlobal('fetch', fetchMock)
    load()
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/hub/me', { credentials: 'same-origin' })
  })

  it('leaves the login link alone on a 401 (signed out)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    load()
    await flush()
    expect(hrefsOf()).toEqual(['/hub/login', '/hub/login', '/'])
  })

  it('leaves the login link alone when the request itself fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    load()
    await flush()
    expect(hrefsOf()).toEqual(['/hub/login', '/hub/login', '/'])
  })

  it('leaves the login link alone when the answer is a 200 with no JSON body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.reject(new SyntaxError('Unexpected end of JSON input')),
      }),
    )
    load()
    await flush()
    expect(hrefsOf()).toEqual(['/hub/login', '/hub/login', '/'])
  })

  it('sends a signed-in member straight to /hub/me', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ role: 'member' }) }),
    )
    load()
    await flush()
    expect(hrefsOf()).toEqual(['/hub/me', '/hub/me', '/'])
  })

  it('sends a signed-in admin straight to /hub/admin', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ role: 'admin' }) }),
    )
    load()
    await flush()
    expect(hrefsOf()).toEqual(['/hub/admin', '/hub/admin', '/'])
  })
})

describe('who loads it', () => {
  const pages = {
    'index.html': readFileSync(join(__dirname, 'index.html'), 'utf-8'),
    'pigrocrm.html': readFileSync(join(__dirname, 'pigrocrm.html'), 'utf-8'),
    'privacy.html': readFileSync(join(__dirname, 'privacy.html'), 'utf-8'),
    'terms.html': readFileSync(join(__dirname, 'terms.html'), 'utf-8'),
  }

  it.each(Object.keys(pages) as (keyof typeof pages)[])(
    '%s loads session.js, whether or not it also loads consent.js',
    (name) => {
      expect(pages[name]).toMatch(/<script type="module" src="\.\/session\.js"><\/script>/)
    },
  )
})
