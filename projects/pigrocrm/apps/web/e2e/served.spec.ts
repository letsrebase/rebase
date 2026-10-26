import { expect, test } from '@playwright/test'

/**
 * Runs against the real compose stack (nginx + api + db), never against the Vite dev
 * server: the routing asserted here lives in deploy/nginx/spa.conf, which the dev
 * server does not use at all, so a suite that only ever hits :5173 would pass while
 * production 404s. Driven by apps/web/scripts/e2e-compose.sh through
 * playwright.compose.config.ts.
 *
 * Since 2026-09-09 this image is the application alone: the public pages are their
 * own project and their own container (`projects/website`). The first test is what
 * keeps the two from quietly growing back together.
 */
test.describe('the served stack', () => {
  test('serves the application and no public page', async ({ page }) => {
    // A copy of the site creeping back into this image is the failure this catches:
    // it would answer 200 here instead of sending the visitor into the CRM.
    const response = await page.goto('/')
    expect(response?.status()).toBe(200)
    expect(new URL(page.url()).pathname).toBe('/app/')
    await expect(page.locator('#root')).toHaveCount(1)
  })

  for (const path of ['/privacy', '/termini', '/community', '/pigrocrm']) {
    test(`${path} is not this stack's to serve`, async ({ page }) => {
      // On the server these four are redirected to letsrebase.com by the host
      // vhost and never reach the container. Reached directly, what must not happen
      // is a 200 with a page in it: the space-name rule reads them as a slug and
      // redirects into the SPA, which is a miss, not a page.
      const response = await page.request.get(path, { maxRedirects: 0 })
      expect(response.status()).not.toBe(200)
    })
  }

  test('/app/ serves the SPA', async ({ page }) => {
    await page.goto('/app/')
    await expect(page.locator('#root')).toHaveCount(1)
  })

  test('/app redirects to /app/, so the prefix location matches', async ({ page }) => {
    // Without `location = /app { return 302 /app/; }` the bare path does not enter
    // `location ^~ /app/` at all and falls through to the catch-all 404.
    await page.goto('/app')
    expect(new URL(page.url()).pathname).toBe('/app/')
  })

  test('a refresh on a deep link still serves the SPA shell', async ({ page }) => {
    const response = await page.goto('/app/customers/00000000-0000-7000-8000-000000000000')
    expect(response?.status()).toBe(200)
    await expect(page.locator('#root')).toHaveCount(1)
  })

  test('the old /login link does not die', async ({ page }) => {
    await page.goto('/login')
    expect(new URL(page.url()).pathname).toBe('/app/login')
  })

  test('/health still reaches the API, unchanged by any of this', async ({ page }) => {
    const response = await page.request.get('/health')
    expect(response.status()).toBe(200)
    expect(await response.json()).toEqual({ status: 'ok' })
  })

  test.describe('the installable app is served at the root and under every space (REB-731)', () => {
    // The manifest and the worker are fetched from the space's own path, which is what
    // makes each space its own app: the general `/<slug>/app/` block answers anything
    // else with the SPA shell, which a browser rejects as a worker.
    for (const prefix of ['', '/studio', '/un-altro-spazio']) {
      test(`${prefix || '(root)'}/app/ answers the manifest, the worker and the offline page`, async ({
        page,
      }) => {
        const manifest = await page.request.get(`${prefix}/app/manifest.webmanifest`)
        expect(manifest.status()).toBe(200)
        expect(manifest.headers()['content-type']).toContain('application/manifest+json')
        expect(manifest.headers()['cache-control']).toBe('no-cache')
        expect((await manifest.json()).scope).toBe('../app')

        const worker = await page.request.get(`${prefix}/app/sw.js`)
        expect(worker.status()).toBe(200)
        expect(worker.headers()['content-type']).toContain('javascript')
        expect(worker.headers()['cache-control']).toBe('no-cache')
        expect(worker.headers()['x-content-type-options']).toBe('nosniff')
        expect(worker.headers()['service-worker-allowed']).toBe(`${prefix}/app`)

        const offline = await page.request.get(`${prefix}/app/offline.html`)
        expect(offline.status()).toBe(200)
        expect(offline.headers()['cache-control']).toBe('no-cache')
        expect(await offline.text()).toContain('Sei offline')
      })
    }

    test('an installed space is its own app: its worker is scoped to it, and works offline', async ({
      page,
      context,
    }) => {
      await page.goto('/studio/app/')
      await page.evaluate(() => navigator.serviceWorker.ready)
      // Asked for by URL: after the first render the router has moved the page to
      // `/studio/app?tab=...`, which is what the scope must contain.
      const scope = await page.evaluate(
        async () => (await navigator.serviceWorker.getRegistration('/studio/app?tab=economica'))?.scope,
      )
      expect(scope).toMatch(/\/studio\/app$/)
      const other = await page.evaluate(
        async () => (await navigator.serviceWorker.getRegistration('/un-altro-spazio/app/'))?.scope,
      )
      expect(other).toBeUndefined()

      await page.reload()
      await context.setOffline(true)
      // The router's home, with no slash after `app`: the page every session returns to.
      await page.goto('/studio/app?tab=economica', { waitUntil: 'load' }).catch(() => {})
      await expect(page.getByRole('heading', { level: 1, name: 'Sei offline' })).toBeVisible()
    })
  })
})
