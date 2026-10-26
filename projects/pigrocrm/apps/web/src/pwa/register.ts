import { isInstallablePage, pwaUrls } from './urls'

export interface PwaEnvironment {
  /** `import.meta.env.PROD`: the dev server never gets a worker (see pwa/plugin.ts). */
  production: boolean
  pathname: string
  /** `tenantPrefix`. */
  prefix: string
  document: Document
  serviceWorker: ServiceWorkerContainer | undefined
}

/**
 * Makes this space installable: points the page at the space's own manifest and
 * registers the space's own worker (REB-731). Everything is a no-op where it cannot
 * apply, and a failure to register never reaches the application: the CRM works the
 * same without a worker, it just cannot be installed or show the offline page.
 *
 * `updateViaCache: 'none'` and the update check on return to the page are what make
 * "replaced by a new deploy" true for an installed app, which may stay open for days
 * without a navigation, the only thing that makes a browser look for a new worker by
 * itself.
 */
export function startPwa(env: PwaEnvironment): void {
  if (!env.production || !isInstallablePage(env.pathname, env.prefix)) return
  const urls = pwaUrls(env.prefix)

  const manifest = env.document.createElement('link')
  manifest.rel = 'manifest'
  manifest.href = urls.manifest
  env.document.head.append(manifest)

  const container = env.serviceWorker
  if (!container) return
  const register = () => {
    container
      .register(urls.worker, { scope: urls.scope, updateViaCache: 'none' })
      .then((registration) => {
        env.document.addEventListener('visibilitychange', () => {
          if (env.document.visibilityState === 'visible') registration.update().catch(() => {})
        })
      })
      .catch((error: unknown) => console.warn('PigroCRM: the service worker was not registered', error))
  }
  // After `load`, so the worker's first fetches never compete with the page's own.
  if (env.document.readyState === 'complete') register()
  else env.document.defaultView?.addEventListener('load', register, { once: true })
}
