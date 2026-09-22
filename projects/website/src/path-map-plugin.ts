import { writeFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join } from 'node:path'
import type { Plugin } from 'vite'

/**
 * The path map, as the dev and preview servers serve it.
 *
 * Production is `deploy/nginx.conf`, inside the website's own image: exact
 * extensionless paths, two redirects, the hashed assets, and a 404 for everything
 * else. Vite's servers know none of that on their own. Left alone they fall back to
 * `index.html` with a 200 for any path that resolves to no file, so a link nobody
 * serves passes the suite locally and 404s in production (ORB-21); and until
 * 2026-09-11 `/` was the community page, not `index.html`, so they served the wrong
 * page at the front door as well (ORB-145 put the landing there). This module says the same thing
 * nginx says, once more, in the shape of a middleware; `path-map-plugin.test.ts` reads
 * `deploy/nginx.conf` and fails when the two copies disagree, which is the only way
 * two copies of anything stay equal.
 */

/** The host every absolute address in this module points at: this file's own
 *  robots.txt, the sitemap it names (REB-110), and every page's canonical and
 *  og:url (REB-111). One constant, so a rebrand (REB-193 already moved it once from
 *  joinorbiters.com) is one line here instead of one per consumer. */
export const SITE_HOST = 'https://letsrebase.com'

/** `location = <path> { try_files <file> =404; }`, one line each in nginx.conf. */
export const PAGES: Readonly<Record<string, string>> = {
  '/': '/index.html',
  '/pigrocrm': '/pigrocrm.html',
  '/pitch': '/pitch.html',
  '/privacy': '/privacy.html',
  '/terms': '/terms.html',
}

/** `location = <path> { return 301 <to>; }`. nginx's `return` drops the query string
 *  and so does this. The community page (`/community`, `/orbiters` before REB-212) is
 *  gone (REB-72), a week after the landing took the front door for good: both its
 *  names now land there instead, and `/termini` is the terms page's name before
 *  REB-318 moved it to `/terms`; all three kept so a bookmark, an ad or a newsletter
 *  link still lands. */
export const REDIRECTS: Readonly<Record<string, string>> = { '/orbiters': '/', '/community': '/', '/termini': '/terms' }

/** Paths nginx serves via `try_files`, exactly like `PAGES`, whose file this plugin
 *  writes at build time instead of Vite building it from an HTML input named in
 *  `vite.config.ts`: robots.txt (REB-109), naming the sitemap, and sitemap.xml
 *  (REB-110), listing every entry in `PAGES` that is not in `NOINDEX`. A page added to
 *  `PAGES` needs no edit here. */
export const GENERATED_PATHS = ['/robots.txt', '/sitemap.xml'] as const
type GeneratedPath = (typeof GENERATED_PATHS)[number]

/** Pages excluded from the sitemap, in the same list shape as `GENERATED_PATHS` since
 *  neither is a `PAGES`-style map from a path to a file: today just `/pitch`, which
 *  carries `<meta name="robots" content="noindex">` (`src/pitch.html:8`) and would
 *  otherwise be the one page in `PAGES` a sitemap tells a crawler to index anyway.
 *  `path-map-plugin.test.ts` reads every page's own head and fails if this list ever
 *  disagrees with it. */
export const NOINDEX = ['/pitch'] as const

/** No `Disallow` line: `/pitch` is the one page a visitor reaches that this site
 *  would rather a crawler skipped, and it already says so with its own
 *  `<meta name="robots" content="noindex">` (`src/pitch.html:8`). Blocking the crawl
 *  in robots.txt too would stop a crawler from ever reaching that tag, and Google's
 *  own guidance is that a page blocked from crawling can still be indexed by an
 *  inbound link with no snippet, worse than the noindex outcome it has today
 *  (developers.google.com/search/docs/crawling-indexing/block-indexing). So robots.txt
 *  disallows nothing a visitor can reach, exactly what REB-109 asked for. */
function robotsTxt(): string {
  return `User-agent: *\nSitemap: ${SITE_HOST}/sitemap.xml\n`
}

/** No `lastmod`, `changefreq` or `priority`: none of them would be true. A build
 *  timestamp on every page every deploy tells a crawler nothing and is worse than
 *  their absence (REB-110). */
function sitemapXml(): string {
  const urls = Object.keys(PAGES)
    .filter((path) => !(NOINDEX as readonly string[]).includes(path))
    .map((path) => `  <url><loc>${SITE_HOST}${path}</loc></url>`)
    .join('\n')
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`
}

/** The content and `Content-Type` for one of `GENERATED_PATHS`, computed fresh on
 *  every call: cheap, and it keeps a single function honest about what ships. */
function generate(pathname: GeneratedPath): { content: string; contentType: string } {
  switch (pathname) {
    case '/robots.txt':
      return { content: robotsTxt(), contentType: 'text/plain; charset=utf-8' }
    case '/sitemap.xml':
      // text/xml, not application/xml: nginx serves the built file from disk under
      // its own mime.types, which maps .xml to text/xml, and this module's whole
      // point is saying the same thing nginx says.
      return { content: sitemapXml(), contentType: 'text/xml; charset=utf-8' }
  }
}

/**
 * Paths the host's vhost (`deploy/letsrebase.conf`) hands to other tenants of the
 * origin before the website container ever sees them. The container 404s them; a
 * visitor never does. A Vite server cannot run those tenants, so it does the one
 * honest thing short of pretending: `/api` is proxied to a running API so the form can
 * be exercised, and the rest answer a plain-text stand-in that says where production
 * sends them. Not a redirect on purpose: `/app` goes to another domain, and a suite
 * that followed it would be testing the CRM's uptime.
 */
export const ELSEWHERE: Readonly<Record<string, string>> = {
  '/api': 'the Orbiters hub API (projects/hub), proxied here to WEBSITE_API_URL',
  '/hub': 'the Orbiters hub SPA (projects/hub)',
  '/app': 'PigroCRM, a 302 to https://pigro.letsrebase.com',
  '/health': "the rebase hub API's probe (projects/hub)",
}

export type Route =
  | { kind: 'page'; file: string }
  | { kind: 'redirect'; to: string }
  | { kind: 'generated'; content: string; contentType: string }
  | { kind: 'proxy' }
  | { kind: 'elsewhere'; owner: string }
  | { kind: 'file' }
  | { kind: 'not-found' }

function underPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`)
}

/** What the server does with one request path (no query string). Pure, so the unit
 *  test can walk the whole map without a server. */
export function route(pathname: string): Route {
  const redirect = REDIRECTS[pathname]
  if (redirect !== undefined) return { kind: 'redirect', to: redirect }
  const file = PAGES[pathname]
  if (file !== undefined) return { kind: 'page', file }
  if ((GENERATED_PATHS as readonly string[]).includes(pathname)) {
    return { kind: 'generated', ...generate(pathname as GeneratedPath) }
  }
  for (const [prefix, owner] of Object.entries(ELSEWHERE)) {
    if (underPrefix(pathname, prefix)) return prefix === '/api' ? { kind: 'proxy' } : { kind: 'elsewhere', owner }
  }
  // nginx serves a page only under the name above it, never as `/privacy.html`.
  if (pathname.endsWith('.html')) return { kind: 'not-found' }
  // Anything with an extension is a file: `/assets/*` in preview, exactly as nginx has
  // it, plus the sources and Vite's own client (`/@vite/client`, `/@fs/...`) in dev. A
  // file that does not exist still 404s, because `appType: 'mpa'` in vite.config.ts
  // turned the index.html fallback off.
  if (pathname.includes('.') || pathname.startsWith('/@')) return { kind: 'file' }
  return { kind: 'not-found' }
}

function handle(req: IncomingMessage, res: ServerResponse, next: () => void): void {
  const [pathname = '/', query] = (req.url ?? '/').split('?')
  const decision = route(pathname)
  switch (decision.kind) {
    case 'redirect':
      res.statusCode = 301
      // With the query string, as nginx does with `$is_args$args`: the links that still
      // say /orbiters or /community are ads and newsletters.
      res.setHeader('Location', `${decision.to}${query ? `?${query}` : ''}`)
      res.end()
      return
    case 'page':
      req.url = `${decision.file}${query ? `?${query}` : ''}`
      next()
      return
    case 'generated':
      res.statusCode = 200
      res.setHeader('Content-Type', decision.contentType)
      res.end(decision.content)
      return
    case 'elsewhere':
      res.statusCode = 200
      res.setHeader('Content-Type', 'text/plain; charset=utf-8')
      res.end(
        `${pathname} is not served by projects/website. In production it belongs to ${decision.owner}; see deploy/letsrebase.conf.\n`,
      )
      return
    case 'not-found':
      res.statusCode = 404
      res.setHeader('Content-Type', 'text/plain; charset=utf-8')
      res.end(
        `404 Not Found: ${pathname} is in neither path map (deploy/nginx.conf, src/path-map-plugin.ts).\n`,
      )
      return
    case 'proxy':
    case 'file':
      next()
  }
}

export function pathMapPlugin(): Plugin {
  return {
    name: 'website-path-map',
    // Plugin middlewares are installed before Vite's own, the proxy included, on both
    // servers; that is what lets this decide `/` before the html fallback does.
    configureServer(server) {
      server.middlewares.use(handle)
    },
    configurePreviewServer(server) {
      server.middlewares.use(handle)
    },
    // GENERATED_PATHS have no HTML input in vite.config.ts for Vite to build, so this
    // writes them straight into the build's output directory once the rest of it
    // exists. `writeBundle` over `generateBundle`: a plain file write needs none of
    // Rollup's asset bookkeeping, and this hook runs only at the end of a real
    // `bundle.write()`, exactly when there is an output directory to write into.
    writeBundle(options) {
      const dir = options.dir
      if (!dir) throw new Error('website-path-map: the build has no output directory, so GENERATED_PATHS cannot be written')
      for (const pathname of GENERATED_PATHS) {
        writeFileSync(join(dir, pathname.slice(1)), generate(pathname).content)
      }
    },
  }
}
