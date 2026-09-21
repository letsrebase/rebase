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
})
