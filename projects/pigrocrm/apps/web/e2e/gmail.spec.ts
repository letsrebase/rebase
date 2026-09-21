import { expect, test } from '@playwright/test'
import { loginAsAdmin, navigate } from './helpers'

/**
 * Spec 13, criterion 5 (e). The revoked state itself is exercised end to end by the
 * backend suite (`GoogleAccountService.health` decides the cause and writes the
 * sentence); what only a browser can prove is that the resulting band is in the *shell*
 * and stays there across a navigation, rather than being a toast that the next page
 * load swallows.
 *
 * The health response is stubbed rather than driven through a real consent flow: this
 * suite has no Google, and `POST /api/gmail/sync` cannot be made to reach one. The
 * stub is the API's own shape, so what is under test is exactly the wiring between it
 * and the shell.
 */
const REVOKED = {
  account: null,
  banner: 'revoked',
  banner_text: 'Il consenso Google per ada@acme.it è stato revocato: ricollega la casella.',
  missing_scopes: [],
  configured: true,
}

test('a revoked credential shows a persistent banner in the app shell', async ({ page }) => {
  await loginAsAdmin(page)
  await page.route('**/api/gmail/account', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(REVOKED),
    })
  })

  await page.goto('/app/customers')
  await expect(page.getByRole('alert').filter({ hasText: 'è stato revocato' })).toBeVisible()

  // Persistent: still there after moving to a different page of the same shell, which
  // is what a toast would not survive.
  await navigate(page, 'Vendite', 'Deal')
  await expect(page).toHaveURL(/\/app\/deal$/)
  await expect(page.getByRole('alert').filter({ hasText: 'è stato revocato' })).toBeVisible()

  // And it links to the one page that can fix it.
  await expect(page.getByRole('link', { name: /Impostazioni → Gmail/ })).toHaveAttribute(
    'href',
    '/app/settings/gmail',
  )
})

test('the banner does not appear on the login screen, and does not even ask', async ({ page }) => {
  // An unauthenticated visitor has no google_accounts row to have an opinion about, so
  // the health query must not be issued at all -- otherwise every load of the login
  // screen collects a 401. Counting the requests is the half of this that an assertion
  // on the absent banner alone cannot make: nothing would have rendered anyway.
  const asked: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/api/gmail/account')) asked.push(request.url())
  })
  await page.route('**/api/gmail/account', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(REVOKED),
    })
  })

  await page.goto('/app/login')
  // Email-first since ORB-172: the login's primary button asks for the link by mail.
  await expect(page.getByRole('button', { name: 'Mandami il link' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(asked).toEqual([])
})
