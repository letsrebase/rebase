/**
 * Dev-server only. Vite serves the SPA under `base: '/app/'` and, on a request for the
 * bare `/app` (no trailing slash), answers with its "did you mean /app/app?" hint page
 * instead of `index.html`. The router lands on exactly that URL after every navigation
 * to Home (`/app?tab=commercial&…`, TanStack strips the trailing slash), so a reload
 * there broke in dev while nginx in production serves it fine. Returns the URL to
 * redirect to, or `null` when the request is not the bare base. `base` is Vite's
 * `config.base` (always with both slashes). A space prefix (`/<slug>/app`) is caught too.
 */
export function redirectToBase(url: string, base: string): string | null {
  const bare = base.replace(/\/$/, '')
  const [path, query] = splitQuery(url)
  if (path !== bare && !path.endsWith(bare)) return null
  if (path !== bare && !/^\/[A-Za-z0-9-]+$/.test(path.slice(0, -bare.length))) return null
  return `${path}/${query}`
}

function splitQuery(url: string): [string, string] {
  const at = url.indexOf('?')
  return at === -1 ? [url, ''] : [url.slice(0, at), url.slice(at)]
}
