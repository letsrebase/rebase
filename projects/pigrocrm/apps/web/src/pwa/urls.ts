import { PUBLIC_APP_ROUTES } from '@/lib/tenant'

/**
 * Where a space's installable app lives (REB-731). A space is served at
 * `/<slug>/app/` and the root installation at `/app/`; `prefix` is `tenantPrefix`
 * (`""` or `"/<slug>"`). The manifest and the worker are files of the build, and nginx
 * serves each of them under every prefix (deploy/nginx/spa.conf), so the URL below is
 * the one that exists for *this* space, and its scope is this space and no other.
 *
 * **The scope has no trailing slash, on purpose.** A scope matches a URL by string
 * prefix, and the router's home is `/<slug>/app?tab=...`, with the slash stripped
 * (`devBaseRedirect.ts` has the history): `/<slug>/app/` would leave the one page every
 * session returns to outside the app, uncontrolled by the worker and, installed, opened
 * in the browser's own toolbar. A worker is only allowed a scope above its own
 * directory by `Service-Worker-Allowed`, which nginx sends for it.
 *
 * The same prefix rule means the root installation's `/app` also matches `/appia/app/...`
 * for a space whose name starts with `app`. Harmless: the root worker passes every page
 * load through, and it only exists where the root is served without a slug.
 */
export function pwaUrls(prefix: string): { scope: string; manifest: string; worker: string } {
  const app = `${prefix}/app`
  return { scope: app, manifest: `${app}/manifest.webmanifest`, worker: `${app}/sw.js` }
}

/**
 * Whether this page belongs to a space that can be installed. The four pages a visitor
 * reaches at the bare `/app/` without a session (`PUBLIC_APP_ROUTES`) are nobody's yet,
 * by design, and on the server the host redirects every other bare `/app/...` path into
 * the root space, so a worker or an install prompt there would be for a space that is
 * not this one. Once a person is in a space the address says so, and this is true.
 */
export function isInstallablePage(pathname: string, prefix: string): boolean {
  if (prefix !== '') return true
  return !PUBLIC_APP_ROUTES.has(pathname.replace(/\/+$/, '').toLowerCase())
}
