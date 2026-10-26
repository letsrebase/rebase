import { expect, test } from '@playwright/test'

/**
 * REB-731, against the production build (`vite preview`, which `e2e.sh` runs, never the
 * dev server: a worker is registered in the production build only). The root
 * installation is served at `/app/`, so the worker's scope here is `/app/`; the same
 * file under `/<slug>/app/` is `served.spec.ts`'s, since only nginx does that.
 */
test.describe('the installable app', () => {
  test('a page links its manifest, which parses and is scoped to the space', async ({ page }) => {
    await page.goto('/app/customers')
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
      'href',
      '/app/manifest.webmanifest',
    )
    const cdp = await page.context().newCDPSession(page)
    const { errors, data } = await cdp.send('Page.getAppManifest')
    expect(errors).toEqual([])
    expect(data).toBeDefined()
    const manifest = JSON.parse(data ?? '')
    expect(manifest).toMatchObject({ name: 'PigroCRM', display: 'standalone' })
    expect(manifest.icons.map((icon: { purpose: string }) => icon.purpose)).toContain('maskable')
    for (const icon of manifest.icons as { src: string }[]) {
      const response = await page.request.get(icon.src)
      expect(response.status(), icon.src).toBe(200)
      expect(response.headers()['content-type']).toBe('image/png')
    }
  })

  test('the worker takes control of the page and keeps nothing a person is shown', async ({
    page,
  }) => {
    await page.goto('/app/customers')
    await page.evaluate(() => navigator.serviceWorker.ready)
    await page.reload()
    expect(await page.evaluate(() => navigator.serviceWorker.controller?.scriptURL)).toMatch(
      /\/app\/sw\.js$/,
    )

    const stored = await page.evaluate(async () => {
      const urls: string[] = []
      for (const name of await caches.keys()) {
        for (const request of await (await caches.open(name)).keys()) urls.push(request.url)
      }
      return urls.map((url) => new URL(url).pathname)
    })
    expect(stored).toContain('/app/offline.html')
    // No document of the application and no API answer, whatever was loaded above.
    expect(stored.filter((path) => path.includes('/api/') || path.endsWith('/'))).toEqual([])
    expect(stored.filter((path) => path.endsWith('index.html'))).toEqual([])
  })

  test('with no network a page load shows the offline page, and the network brings it back', async ({
    page,
    context,
  }) => {
    await page.goto('/app/customers')
    await page.evaluate(() => navigator.serviceWorker.ready)
    await page.reload()

    await context.setOffline(true)
    await page.goto('/app/customers', { waitUntil: 'load' }).catch(() => {})
    await expect(page.getByRole('heading', { level: 1, name: 'Sei offline' })).toBeVisible()
    expect(new URL(page.url()).pathname).toBe('/app/customers')

    // Still offline: asking again gets the same page, not an error.
    await page.getByRole('button', { name: 'Riprova' }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Sei offline' })).toBeVisible()

    // The page reloads itself the moment the network is back, with no click.
    await context.setOffline(false)
    await expect(page).toHaveURL(/\/app\/login/)
    await expect(page.getByRole('heading', { name: 'Sei offline' })).toHaveCount(0)
  })

  test('a redirect still reaches the browser: the worker does not follow it', async ({ page }) => {
    await page.goto('/app/customers')
    await page.evaluate(() => navigator.serviceWorker.ready)
    await page.reload()
    // `/app` answers a 302 to `/app/`: followed by the browser, not turned into an error page.
    await page.goto('/app')
    await expect(page.getByRole('heading', { name: 'Sei offline' })).toHaveCount(0)
    await expect(page.locator('#root')).toHaveCount(1)
  })
})
