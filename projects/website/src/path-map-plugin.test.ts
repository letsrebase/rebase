import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ELSEWHERE, GENERATED_PATHS, NOINDEX, PAGES, REDIRECTS, SITE_HOST, pathMapPlugin, route } from './path-map-plugin'

const nginx = readFileSync(join(__dirname, '..', 'deploy', 'nginx.conf'), 'utf-8')
const vhost = readFileSync(join(__dirname, '..', 'deploy', 'letsrebase.conf'), 'utf-8')

/** The exact-match locations of nginx.conf, as two maps shaped like the plugin's own. */
function nginxMap(conf: string): { pages: Record<string, string>; redirects: Record<string, string> } {
  const pages: Record<string, string> = {}
  const redirects: Record<string, string> = {}
  for (const [, path, file, to] of conf.matchAll(
    /^\s*location = (\S+)\s*\{\s*(?:try_files (\S+) =404|return 301 (\S+));\s*\}/gm,
  )) {
    if (file) pages[path!] = file
    // `$is_args$args` is how nginx says "carry the query string", which the dev server
    // does in code instead (`handle`), so the target compared here is the path alone.
    if (to) redirects[path!] = to.replace('$is_args$args', '')
  }
  return { pages, redirects }
}

describe('the path map, against deploy/nginx.conf', () => {
  it('serves the same file at each path nginx does, and no other', () => {
    // GENERATED_PATHS have no rollup input in vite.config.ts; this plugin writes them
    // into the build output itself (see the robots.txt describe block below), but
    // nginx serves them with the same `try_files <path> =404` shape as every page, so
    // they still have to appear here for the two files to agree.
    const generatedAsPages = Object.fromEntries(GENERATED_PATHS.map((path) => [path, path]))
    expect({ ...PAGES, ...generatedAsPages }).toEqual(nginxMap(nginx).pages)
  })

  it('redirects the same paths to the same places', () => {
    expect(REDIRECTS).toEqual(nginxMap(nginx).redirects)
  })

  it('carries the query string through the redirect, so an old ad link keeps its utm_*', () => {
    expect(nginx).toMatch(/location = \/orbiters \{ return 301 \/\$is_args\$args; \}/)
    expect(nginx).toMatch(/location = \/community \{ return 301 \/\$is_args\$args; \}/)
  })

  it('reads at least the four pages out of nginx.conf, so a reformatted file cannot pass as an empty map', () => {
    expect(Object.keys(nginxMap(nginx).pages).length).toBeGreaterThanOrEqual(4)
  })

  it('mirrors an nginx that 404s everything it was not told about', () => {
    expect(nginx).toMatch(/location \/ \{ return 404; \}/)
    expect(nginx).not.toMatch(/try_files \$uri \/index\.html/)
  })

  it('names only tenants the host vhost actually routes away from the container', () => {
    for (const prefix of Object.keys(ELSEWHERE)) {
      expect(vhost, `${prefix} in letsrebase.conf`).toMatch(
        new RegExp(`^\\s*location\\s+(?:=|\\^~)?\\s*${prefix}[/\\s]`, 'm'),
      )
    }
  })
})

describe('route', () => {
  it('puts the landing at the front door, and redirects the community page\'s two old names there (ORB-145, REB-72, REB-318)', () => {
    expect(route('/')).toEqual({ kind: 'page', file: '/index.html' })
    expect(route('/community')).toEqual({ kind: 'redirect', to: '/' })
    expect(route('/orbiters')).toEqual({ kind: 'redirect', to: '/' })
    expect(route('/pitch')).toEqual({ kind: 'page', file: '/pitch.html' })
    expect(route('/privacy')).toEqual({ kind: 'page', file: '/privacy.html' })
    expect(route('/terms')).toEqual({ kind: 'page', file: '/terms.html' })
    expect(route('/termini')).toEqual({ kind: 'redirect', to: '/terms' })
  })

  it('serves PigroCRM its own page at /pigrocrm again (ORB-159), and keeps no redirect of its own', () => {
    expect(route('/pigrocrm')).toEqual({ kind: 'page', file: '/pigrocrm.html' })
    expect(REDIRECTS['/pigrocrm']).toBeUndefined()
  })

  it('404s what nginx 404s: unknown paths, trailing slashes, and the files under their own names', () => {
    for (const path of ['/nonexistent', '/pigrocrm/', '/privacy/', '/index.html', '/community.html', '/privacy.html']) {
      expect(route(path), path).toEqual({ kind: 'not-found' })
    }
  })

  it('lets the built assets, the sources and the dev client through, with no html fallback behind them', () => {
    for (const path of ['/assets/landing-BwJRpj9t.css', '/landing.js', '/rebase-logo.svg', '/@vite/client', '/@fs/x/y.ts']) {
      expect(route(path), path).toEqual({ kind: 'file' })
    }
  })

  it('proxies /api and answers a stand-in for the other tenants of the origin, whole prefixes only', () => {
    expect(route('/api/community/signups')).toEqual({ kind: 'proxy' })
    expect(route('/app/')).toMatchObject({ kind: 'elsewhere' })
    expect(route('/app')).toMatchObject({ kind: 'elsewhere' })
    expect(route('/hub/freelance')).toMatchObject({ kind: 'elsewhere' })
    expect(route('/health')).toMatchObject({ kind: 'elsewhere' })
    // `/apple` is not `/app/`: a prefix match that ignored the slash would hand a real
    // 404 to a fictional tenant.
    expect(route('/apple')).toEqual({ kind: 'not-found' })
    expect(route('/hubris')).toEqual({ kind: 'not-found' })
  })
})

describe('robots.txt (REB-109)', () => {
  it('has its own exact-match location in nginx.conf, like every page', () => {
    expect(nginx).toMatch(/location = \/robots\.txt \{ try_files \/robots\.txt =404; \}/)
  })

  it('names the sitemap and disallows nothing a visitor can reach', () => {
    const decision = route('/robots.txt')
    expect(decision.kind).toBe('generated')
    if (decision.kind !== 'generated') throw new Error('unreachable')
    expect(decision.contentType).toBe('text/plain; charset=utf-8')
    expect(decision).toMatchObject({
      content: `User-agent: *\nSitemap: ${SITE_HOST}/sitemap.xml\n`,
    })
  })

  it('is written into the build output by the plugin\'s own writeBundle hook', () => {
    const dir = mkdtempSync(join(tmpdir(), 'website-robots-'))
    try {
      const plugin = pathMapPlugin()
      if (typeof plugin.writeBundle !== 'function') throw new Error('writeBundle is not a plain function')
      plugin.writeBundle.call({} as never, { dir } as never, {} as never)
      expect(readFileSync(join(dir, 'robots.txt'), 'utf-8')).toContain(`Sitemap: ${SITE_HOST}/sitemap.xml`)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('throws rather than silently skipping the write when the build has no output directory', () => {
    const { writeBundle } = pathMapPlugin()
    if (typeof writeBundle !== 'function') throw new Error('writeBundle is not a plain function')
    expect(() => writeBundle.call({} as never, {} as never, {} as never)).toThrow(/output directory/)
  })
})

describe('sitemap.xml (REB-110)', () => {
  it('has its own exact-match location in nginx.conf, like robots.txt', () => {
    expect(nginx).toMatch(/location = \/sitemap\.xml \{ try_files \/sitemap\.xml =404; \}/)
  })

  it('lists exactly the pages in PAGES that are not in NOINDEX, absolute, on SITE_HOST, and no others', () => {
    const decision = route('/sitemap.xml')
    expect(decision.kind).toBe('generated')
    if (decision.kind !== 'generated') throw new Error('unreachable')
    expect(decision.contentType).toBe('text/xml; charset=utf-8')
    const locs = [...decision.content.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1])
    const expected = Object.keys(PAGES)
      .filter((path) => !(NOINDEX as readonly string[]).includes(path))
      .map((path) => `${SITE_HOST}${path}`)
    // Both directions: a `<loc>` the generator emits for a page this set excludes, and
    // a page in the set the generator leaves out. Sorted `toEqual` fails on either
    // side being longer, not only on a mismatched element. A page removed from PAGES
    // legitimately leaves the sitemap; the nginx parity test above is what catches it
    // being removed from one map and not the other.
    expect([...locs].sort()).toEqual([...expected].sort())
  })

  it('carries no lastmod, changefreq or priority', () => {
    const decision = route('/sitemap.xml')
    if (decision.kind !== 'generated') throw new Error('unreachable')
    expect(decision.content).not.toMatch(/lastmod|changefreq|priority/)
  })

  it('is written into the build output by the writeBundle hook, alongside robots.txt', () => {
    const dir = mkdtempSync(join(tmpdir(), 'website-sitemap-'))
    try {
      const { writeBundle } = pathMapPlugin()
      if (typeof writeBundle !== 'function') throw new Error('writeBundle is not a plain function')
      writeBundle.call({} as never, { dir } as never, {} as never)
      expect(readFileSync(join(dir, 'sitemap.xml'), 'utf-8')).toContain(`${SITE_HOST}/pigrocrm`)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('NOINDEX, against every page\'s own head', () => {
  it('matches exactly the pages that declare <meta name="robots" content="noindex">', () => {
    const noindex = /<meta\s[^>]*name="robots"[^>]*content="[^"]*\bnoindex\b/
    const actuallyNoindex = Object.entries(PAGES)
      .filter(([, file]) => noindex.test(readFileSync(join(__dirname, file.slice(1)), 'utf-8')))
      .map(([path]) => path)
      .sort()
    expect([...NOINDEX].sort()).toEqual(actuallyNoindex)
  })
})
