import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * The Italian addresses a mail, a digest, a bookmark or a push payload may still carry
 * (the English rename of 2026-11-02 changed the page names and the query words): each
 * opens the screen it always did.
 */
test.describe('legacy links', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page)
  })

  test('/app/oggi opens the Today view of the Tasks page, keeping ?da=digest as ?from=digest', async ({ page }) => {
    await page.goto('/app/oggi?da=digest')
    await expect(page).toHaveURL(/\/app\/activities\?.*view=today/)
    await expect(page).toHaveURL(/from=digest/)
    await expect(page.getByRole('tab', { name: 'Oggi' })).toHaveAttribute('aria-selected', 'true')
  })

  test('/app/avvisi opens the reminders page', async ({ page }) => {
    await page.goto('/app/avvisi')
    await expect(page).toHaveURL(/\/app\/reminders$/)
  })

  test('the Italian settings pages open their English ones', async ({ page }) => {
    await page.goto('/app/settings/aziende')
    await expect(page).toHaveURL(/\/app\/settings\/legal-entities$/)
    await page.goto('/app/settings/template')
    await expect(page).toHaveURL(/\/app\/settings\/templates$/)
    await page.goto('/app/impostazioni/template')
    await expect(page).toHaveURL(/\/app\/settings\/templates$/)
  })

  test('an Italian dashboard tab and period still open the same tab', async ({ page }) => {
    await page.goto('/app/?tab=anno&da=2026-01-01&a=2026-12-31')
    await expect(page).toHaveURL(/tab=year/)
    await expect(page).toHaveURL(/from=2026-01-01/)
    await expect(page).toHaveURL(/to=2026-12-31/)
  })

  test('?apri=, ?assegnata= and ?vista= open the task list as ?open=, ?assigned= and ?view=', async ({ page }) => {
    await page.goto('/app/activities?assegnata=tutte&vista=tutte')
    await expect(page).toHaveURL(/view=all/)
    await expect(page).toHaveURL(/assigned=all/)
    await expect(page.getByRole('tab', { name: 'Tutte' })).toHaveAttribute('aria-selected', 'true')
  })

  test('?scadute=, ?da_fatturare= and ?fatturato_non_vinto= keep their filters', async ({ page }) => {
    await page.goto('/app/invoices?scadute=true')
    await expect(page).toHaveURL(/overdue=true/)
    await page.goto('/app/deal/list?da_fatturare=true')
    await expect(page).toHaveURL(/to_invoice=true/)
  })
})
