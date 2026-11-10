import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * Impostazioni → Caselle (REB-1044), against the real API. No IMAP server runs in this stack,
 * so what the form can show end to end is the API's own refusal of a mailbox it could not log
 * into: the page must say it, keep what was typed and list nothing new.
 */
test('an admin opens Caselle, and a mailbox the server cannot reach is refused in words', async ({
  page,
}) => {
  await loginAsAdmin(page)
  await page.goto('/app/settings/mailboxes')
  await expect(page.getByRole('tab', { name: 'Caselle' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Caselle collegate' })).toBeVisible()

  await page.getByLabel('Indirizzo della casella').fill('fatture@invalid.example')
  await page.getByLabel('Server IMAP').fill('imap.invalid.example')
  await page.getByLabel('Utente').fill('fatture')
  await page.getByLabel('Password').fill('non-importa')
  await page.getByRole('button', { name: 'Collega la casella' }).click()

  await expect(page.getByRole('alert').filter({ hasText: /\S/ })).toBeVisible()
  await expect(page.getByLabel('Indirizzo della casella')).toHaveValue('fatture@invalid.example')
  await expect(page.getByRole('list', { name: 'Caselle collegate' })).toHaveCount(0)
})
