/**
 * The web app manifest (REB-731), one file for every space.
 *
 * A space lives at `/<slug>/app/`, so `start_url` and `scope` are written relative:
 * the browser resolves them against the URL the manifest is fetched from, and nginx
 * serves this same file at `/<slug>/app/manifest.webmanifest` for every slug
 * (deploy/nginx/spa.conf). The SPA points its `<link rel="manifest">` at the one under
 * its own prefix (`src/pwa/urls.ts`), which is what makes each space its own installed
 * app, opening on that space from the icon.
 *
 * `scope` is `../app`, which from `/<slug>/app/manifest.webmanifest` is `/<slug>/app`:
 * no trailing slash, because the router's home is `/<slug>/app?tab=...` and a scope
 * matches by string prefix (`src/pwa/urls.ts` has the reason). `start_url` is the
 * directory, `/<slug>/app/`, which that scope contains.
 *
 * No `id`, on purpose. The spec resolves an `id` against the *origin*, so a relative one
 * would be the same for every space and the browser would take the second space for an
 * update of the first. Left out, the id is the resolved `start_url`, which differs.
 */
export interface ManifestIcon {
  src: string
  sizes: string
  type: 'image/png'
  purpose: 'any' | 'maskable'
}

export interface ManifestInput {
  /** Both `background_color` and `theme_color`: the Paper ground the app is drawn on. */
  ground: string
  icons: readonly ManifestIcon[]
}

export function buildManifest({ ground, icons }: ManifestInput) {
  return {
    name: 'PigroCRM',
    short_name: 'PigroCRM',
    description: 'Il CRM che lavora al posto tuo.',
    lang: 'it',
    start_url: './',
    scope: '../app',
    display: 'standalone',
    background_color: ground,
    theme_color: ground,
    icons,
  }
}
