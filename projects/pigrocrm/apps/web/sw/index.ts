/// <reference lib="webworker" />

/**
 * PigroCRM's service worker (REB-731).
 *
 * One rule decides everything in this file: **nothing a person is shown is ever kept
 * here.** No page, no API answer, no figure. Cache Storage holds the offline page and
 * the few files it is drawn with, all of them produced by the build before anybody has
 * a session, so there is nothing in them that could be stale or belong to somebody
 * else. A page load goes to the network and, when the network is gone, gets the offline
 * page in its place; everything else is left to the browser (`decideFetch`).
 *
 * That is also why this worker can safely replace itself the moment a deploy changes
 * it: `skipWaiting` and `clients.claim` cost no open tab anything, because the
 * application's own code never came through here.
 *
 * **Scope.** The worker is registered per space, fetched from `/<slug>/app/sw.js` with
 * the scope `/<slug>/app` (`src/pwa/urls.ts` says why it has no trailing slash), and
 * nginx serves the one file under every slug with the `Service-Worker-Allowed` header
 * that lets it (deploy/nginx/spa.conf). A worker scoped to `/` would see every space and
 * the API of all of them.
 *
 * **Adding a feature** (web push, REB-738, is the first): put its listeners in their own
 * file under `sw/` and import it below, beside `registerShell`. Each feature owns its
 * events and its caches; `staleCacheNames` only ever deletes this worker's.
 *
 * Checked by `tsconfig.sw.json`, whose lib is `webworker` and not `dom`. The handler
 * test (`pwa/sw-handlers.test.ts`) imports this file into the main program too, which
 * only works because `skipLibCheck` lets the two libs coexist there: nothing here may
 * use a DOM-only type, and nothing in the page may import it.
 */
import { decideFetch, shellCacheName, staleCacheNames } from './policy'

declare const __BUILD_ID__: string
declare const __SHELL_PATHS__: readonly string[]

const sw = self as unknown as ServiceWorkerGlobalScope

const CACHE = shellCacheName(sw.registration.scope, __BUILD_ID__)
const SHELL_PATHS: ReadonlySet<string> = new Set(__SHELL_PATHS__)

/** The offline page, next to this worker: the same file for every space, at the one URL
 *  nginx serves it from for that space. Not relative to the scope, which has no trailing
 *  slash and so is not a directory. */
function offlineUrl(): string {
  return new URL('offline.html', sw.location.href).href
}

function registerShell(): void {
  sw.addEventListener('install', (event) => {
    event.waitUntil(precache().then(() => sw.skipWaiting()))
  })

  sw.addEventListener('activate', (event) => {
    event.waitUntil(dropStaleCaches().then(() => sw.clients.claim()))
  })

  sw.addEventListener('fetch', (event) => {
    const { request } = event
    const decision = decideFetch({
      method: request.method,
      mode: request.mode,
      url: new URL(request.url),
      origin: sw.location.origin,
      shellPaths: SHELL_PATHS,
    })
    if (decision === 'navigate') event.respondWith(navigate(request))
    else if (decision === 'shell') event.respondWith(shellFile(request))
    // 'network': no respondWith, so the browser handles it as if no worker existed.
  })
}

async function precache(): Promise<void> {
  const cache = await caches.open(CACHE)
  const urls = [offlineUrl(), ...__SHELL_PATHS__.map((path) => new URL(path, sw.location.origin).href)]
  // `reload`: never fill a deploy's cache from the HTTP cache of the previous one.
  await cache.addAll(urls.map((url) => new Request(url, { cache: 'reload' })))
}

async function dropStaleCaches(): Promise<void> {
  const stale = staleCacheNames(await caches.keys(), CACHE, sw.registration.scope)
  await Promise.all(stale.map((name) => caches.delete(name)))
}

/**
 * The request itself goes to `fetch`, not a rebuilt one: a navigation carries
 * `redirect: 'manual'`, so a redirect (the login's, an OAuth hop to another origin) is
 * handed back to the browser to follow, instead of this worker following it and being
 * refused for answering a same-origin page with another origin's response.
 *
 * **`cache: 'no-cache'` is what makes the offline page appear at all.** The SPA's
 * `index.html` comes with a `Last-Modified` and no `Cache-Control`, so the browser may
 * treat it as fresh for a tenth of its age (hours, for a deploy a day old) and a
 * `fetch` that does not say otherwise answers from the HTTP cache with no network at
 * all: offline, the application would load from disk and fail on its first API call,
 * and the page this worker exists for would never be shown. `no-cache` asks the server
 * every time (a 304 when nothing changed), which offline is an error, and it is what a
 * deploy needs too: the new shell reaches an installed app on its next page load.
 */
async function navigate(request: Request): Promise<Response> {
  try {
    return await fetch(request, { cache: 'no-cache' })
  } catch {
    return offlinePage()
  }
}

/** 503, not 200: the page the person asked for was not served. The body is the offline
 *  page; `no-store` keeps the browser from remembering it as that address's content. */
async function offlinePage(): Promise<Response> {
  const cached = await (await caches.open(CACHE)).match(offlineUrl(), { ignoreVary: true })
  if (!cached) return Response.error()
  const headers = new Headers(cached.headers)
  headers.set('Cache-Control', 'no-store')
  return new Response(cached.body, { status: 503, statusText: 'Service Unavailable', headers })
}

/** `ignoreVary`: these are the build's own files, one copy each, stored under the request
 *  that fetched them. A server that answers `Vary: Origin` (vite preview does) would
 *  otherwise make a script tag's request, which differs from that one in headers only,
 *  miss its own file and fail offline. */
async function shellFile(request: Request): Promise<Response> {
  const cached = await (await caches.open(CACHE)).match(request, { ignoreVary: true })
  return cached ?? fetch(request)
}

registerShell()
