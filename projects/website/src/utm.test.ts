import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

/**
 * utm.js carries where a visitor came from into the hub: the six UTM keys of the
 * page's URL (ORB-166) and the page itself as `da=` (ORB-167) go onto every link into
 * `/hub/`, nothing else is touched, and both are remembered for the tab under the keys
 * the hub reads. Shared by every page with a door into the hub -- `landing.js` for `/`
 * and `/pigrocrm` -- so this is the one place the behaviour is pinned, rather than
 * once per caller.
 */
const source = readFileSync(join(__dirname, 'utm.js'), 'utf-8')

interface Utm {
  carryUtm: (root?: ParentNode, search?: string, pathname?: string) => number
  readUtm: (search: string) => URLSearchParams
  pageSlug: (pathname?: string) => string
  UTM_KEYS: readonly string[]
  UTM_STORAGE_KEY: string
  ORIGIN_STORAGE_KEY: string
}

function load(): Utm {
  new Function(source)()
  const api = (window as Window & { __utm?: Utm }).__utm
  if (!api) throw new Error('utm.js did not expose window.__utm')
  return api
}

function fixture(hrefs: string[]) {
  const root = document.createElement('div')
  root.innerHTML = hrefs.map((href) => `<a href="${href}">x</a>`).join('')
  return root
}

const hrefsOf = (root: ParentNode) => [...root.querySelectorAll('a')].map((a) => a.getAttribute('href'))

beforeEach(() => window.sessionStorage.clear())
afterEach(() => window.sessionStorage.clear())

it('stays small', () => {
  // Loaded by every page with a door into the hub, before the script that calls it.
  expect(Buffer.byteLength(source, 'utf-8')).toBeLessThan(5 * 1024)
})

describe('carryUtm', () => {
  it('appends the UTM keys and the page to every link into the hub, and to nothing else', () => {
    const utm = load()
    const root = fixture(['/hub/freelance', '/hub/aziende', '/hub/freelance?perk=guida', '/privacy', 'https://pigro.letsrebase.com/app/'])
    const rewritten = utm.carryUtm(root, '?utm_source=linkedin&utm_campaign=orbita&utm_id=42&gclid=nope&utm_term=%20', '/')
    expect(rewritten).toBe(3)
    expect(hrefsOf(root)).toEqual([
      '/hub/freelance?utm_source=linkedin&utm_campaign=orbita&utm_id=42&da=home',
      '/hub/aziende?utm_source=linkedin&utm_campaign=orbita&utm_id=42&da=home',
      '/hub/freelance?perk=guida&utm_source=linkedin&utm_campaign=orbita&utm_id=42&da=home',
      '/privacy',
      'https://pigro.letsrebase.com/app/',
    ])
  })

  it('remembers the campaign and the page for the tab under the names the hub reads', () => {
    const utm = load()
    utm.carryUtm(fixture(['/hub/freelance']), '?utm_source=linkedin&utm_medium=paid', '/pigrocrm')
    expect(utm.UTM_STORAGE_KEY).toBe('orbiters.utm')
    expect(utm.ORIGIN_STORAGE_KEY).toBe('orbiters.da')
    expect(window.sessionStorage.getItem('orbiters.utm')).toBe('utm_source=linkedin&utm_medium=paid')
    expect(window.sessionStorage.getItem('orbiters.da')).toBe('pigrocrm')
  })

  it('never overrides a key a link already carries', () => {
    const utm = load()
    const root = fixture(['/hub/freelance?utm_source=newsletter&da=altro'])
    utm.carryUtm(root, '?utm_source=linkedin&utm_campaign=orbita', '/')
    expect(hrefsOf(root)).toEqual(['/hub/freelance?utm_source=newsletter&da=altro&utm_campaign=orbita'])
  })

  it('says the page even when there is no campaign, and remembers no campaign', () => {
    const utm = load()
    const root = fixture(['/hub/freelance', '/hub/aziende'])
    expect(utm.carryUtm(root, '?ref=friend', '/pigrocrm')).toBe(2)
    expect(hrefsOf(root)).toEqual(['/hub/freelance?da=pigrocrm', '/hub/aziende?da=pigrocrm'])
    expect(window.sessionStorage.getItem('orbiters.utm')).toBeNull()
    expect(window.sessionStorage.getItem('orbiters.da')).toBe('pigrocrm')
  })

  it('names the page by its path, home for the front door', () => {
    const utm = load()
    expect(utm.pageSlug('/')).toBe('home')
    expect(utm.pageSlug('/pigrocrm')).toBe('pigrocrm')
    expect(utm.pageSlug('/pigrocrm/')).toBe('pigrocrm')
    expect(utm.pageSlug('/community')).toBe('community')
    expect(utm.pageSlug('/Strana Pagina!')).toBe('strana-pagina-')
  })

  it('caps a value at 200 characters, like the hub does', () => {
    const utm = load()
    const long = 'x'.repeat(250)
    expect(utm.readUtm(`?utm_content=${long}`).get('utm_content')).toHaveLength(200)
  })
})

describe('who calls it', () => {
  // Pinned on the caller's own source, the same way typewriter.test.ts pins that
  // both scripts call `window.__typewriter`.
  const callers = {
    'landing.js': readFileSync(join(__dirname, 'landing.js'), 'utf-8'),
  }
  const pages = {
    'index.html': readFileSync(join(__dirname, 'index.html'), 'utf-8'),
    'pigrocrm.html': readFileSync(join(__dirname, 'pigrocrm.html'), 'utf-8'),
  }

  it.each(Object.keys(callers) as (keyof typeof callers)[])('%s calls window.__utm.carryUtm()', (name) => {
    expect(callers[name]).toContain('window.__utm.carryUtm()')
  })

  it.each(Object.keys(pages) as (keyof typeof pages)[])('%s loads utm.js before the script that calls it', (name) => {
    const page = pages[name]
    expect(page).toMatch(/<script type="module" src="\.\/utm\.js"><\/script>/)
    const utmIndex = page.indexOf('<script type="module" src="./utm.js">')
    const callerIndex = page.indexOf('<script type="module" src="./landing.js">')
    expect(callerIndex).toBeGreaterThan(0)
    expect(utmIndex).toBeLessThan(callerIndex)
  })
})
