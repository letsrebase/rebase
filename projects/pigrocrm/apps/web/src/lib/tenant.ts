/**
 * Which space this page belongs to, read from the URL once at start-up.
 *
 * The SPA is one bundle served at `/app/` and, for a space, at `/<slug>/app/` by the
 * same nginx (deploy/nginx/spa.conf). Everything that builds a URL -- the router's
 * basepath, the API client's baseUrl -- takes the prefix from here, so the rest of the
 * application never spells a tenant. The root installation has the empty prefix.
 *
 * The grammar mirrors `pigrocrm.core.tenants.schemas.SLUG_PATTERN` and its reserved
 * words: `/app/...` is the root, never a space called "app".
 */

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$/
export const SLUG_MAX = 32

export const RESERVED_SLUGS = new Set([
  'app',
  'api',
  'health',
  'assets',
  'privacy',
  'termini',
  'orbiters',
  'pigrocrm',
  'pigro',
  'login',
  'www',
  'admin',
  'static',
  'registrati',
  'mcp',
])

/** `"/studio"` for `/studio/app/customers`, `""` for `/app/customers` or anything else. */
export function tenantPrefixFrom(pathname: string): string {
  const match = /^\/([a-z0-9][a-z0-9-]{1,30}[a-z0-9])\/app(?:\/|$)/.exec(pathname)
  if (!match) return ''
  const slug = match[1] ?? ''
  return RESERVED_SLUGS.has(slug) ? '' : `/${slug}`
}

/** Same rule as `pigrocrm.core.tenants.schemas.slugify`, so the page's preview is what
 *  the server will accept: accents stripped, runs of anything else become one hyphen,
 *  trimmed, lowercased, cut to 32. */
export function slugify(nome: string): string {
  const ascii = nome
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^\u0020-\u007e]/g, '')
  return ascii
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, SLUG_MAX)
    .replace(/-+$/g, '')
}

/** Why a slug would be refused, in the words the server uses, or null when it is fine.
 *  Checked here first so the person sees it while typing, and checked again by the API. */
export function slugProblem(slug: string): string | null {
  if (slug.length < 3) return 'il nome deve avere almeno 3 caratteri'
  if (slug.length > SLUG_MAX) return `il nome può avere al massimo ${SLUG_MAX} caratteri`
  if (!SLUG_PATTERN.test(slug))
    return "solo lettere minuscole, cifre e trattini, senza trattini all'inizio o alla fine"
  if (RESERVED_SLUGS.has(slug)) return 'questo nome è riservato'
  return null
}

export const tenantPrefix: string =
  typeof window === 'undefined' ? '' : tenantPrefixFrom(window.location.pathname)

/** Where a space logs in, as a full-page destination: a different basepath is a
 *  different application instance, so it is a navigation, not a router push. */
export function spaceLoginUrl(slug: string): string {
  return `/${slug}/app/login`
}

/** The four pages a visitor reaches under `/app` without a session (routes/app.tsx's
 *  guard never bounces them): the login, the signup that makes a space (spec
 *  2026-09-08), the page that spends a link by mail (spec 2026-09-12 §6.2) and the page
 *  that spends an invitation (spec 2026-09-17, REB-291). Shared with the guard so the
 *  two checks below cannot drift apart. */
export const PUBLIC_APP_ROUTES = new Set([
  '/app/login',
  '/app/register',
  '/app/verify',
  '/app/invite',
])

/**
 * Whether a `redirect` search value captured by `/app`'s guard is safe to send a
 * freshly authenticated visitor to. The guard only ever records the router's own
 * basepath-relative `href` (never the tenant prefix, never another origin), so a
 * legitimate value always resolves under `/app/`; a hand-edited query string could
 * claim anything, including a scheme, a protocol-relative address, or a `..` segment
 * walking back out of `/app` (plain or percent-encoded: `%2e%2e` is a dot segment to
 * the URL parser exactly as `..` is), so the value is parsed and normalized by `URL`
 * itself -- the same parser `navigate({ href })` uses -- rather than pattern-matched.
 * `PUBLIC_APP_ROUTES` is excluded too, case- and trailing-slash-insensitively and
 * after decoding, since the guard never records them (they are the pages a visitor
 * without a session already reaches): a value naming one, in any spelling, is not a
 * deep link that got interrupted, it is a query string someone wrote by hand.
 */
export function safeAppRedirect(target: string | undefined): string | undefined {
  if (!target || !target.startsWith('/') || target.startsWith('//')) return undefined
  let parsed: URL
  try {
    parsed = new URL(target, 'http://internal.invalid')
  } catch {
    return undefined
  }
  if (parsed.origin !== 'http://internal.invalid') return undefined
  let decodedPath: string
  try {
    decodedPath = decodeURIComponent(parsed.pathname)
  } catch {
    return undefined
  }
  const normalized = decodedPath.toLowerCase().replace(/\/+$/, '')
  if (!normalized.startsWith('/app/') || PUBLIC_APP_ROUTES.has(normalized)) return undefined
  return decodedPath + parsed.search + parsed.hash
}
