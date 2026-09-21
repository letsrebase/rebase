import { expect, test } from '@playwright/test'
import { login, loginAsAdmin, logout } from './helpers'

/**
 * `collaboratore` sits deliberately in the middle of this product's three roles:
 * it can write records (unlike `readonly`) but administers nothing
 * (`SettingsLayout.tsx`'s own docstring: Campi/Pipeline/Utenti are admin-only at
 * the service layer). Access tokens are the one screen that specifically must
 * NOT follow that gate -- `PatService` scopes a token by `actor.id`, never by
 * role, and a token is how a human connects an agent (Claude, via the MCP
 * server) to their own account. Gating it to admins would mean a collaborator
 * could never do that at all, which is exactly the mistake `routes/app/token.tsx`'s
 * own comment calls out.
 */
test('a collaboratore is not offered Settings and cannot reach it by URL, but can reach their own tokens', async ({
  page,
}) => {
  const email = `collaboratore-${Date.now()}@pigro.it`
  const password = 'collabora123'

  // Admin creates the collaboratore -- there is no public registration.
  await loginAsAdmin(page)
  await page.goto('/app/settings/users')
  await page.getByRole('button', { name: /nuovo utente/i }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Nome').fill('Collaboratore E2E')
  await dialog.getByLabel('Email').fill(email)
  await dialog.getByLabel('Password').fill(password)
  // "Ruolo" already defaults to "collaboratore" (UsersPanel's own `useState`) --
  // left untouched rather than re-selecting the same value through the Select.
  await dialog.getByRole('button', { name: 'Crea' }).click()
  await expect(dialog).toBeHidden()

  await logout(page)

  await login(page, email, password)

  // Not offered: AppShell's nav only renders the Impostazioni link `isAdmin &&`.
  await expect(page.getByRole('link', { name: 'Impostazioni' })).not.toBeVisible()

  // Cannot reach it by typing the URL either -- SettingsLayout itself refuses,
  // in place, with no redirect (routes/app.tsx's own guard only checks "is
  // anyone logged in", not role).
  await page.goto('/app/settings/fields')
  await expect(page.getByText('Accesso riservato')).toBeVisible()
  await expect(page.getByRole('tab')).toHaveCount(0)

  // ...but *can* reach, and fully use, their own access tokens.
  await page.goto('/app/token')
  await expect(page.getByRole('heading', { name: 'Token di accesso' })).toBeVisible()
  await page.getByRole('button', { name: /nuovo token/i }).click()
  const tokenDialog = page.getByRole('dialog')
  await tokenDialog.getByLabel('Nome').fill('Token E2E collaboratore')
  await tokenDialog.getByRole('button', { name: 'Crea' }).click()

  const revealDialog = page.getByRole('dialog').filter({ hasText: 'Token creato' })
  await expect(revealDialog).toBeVisible()
  await revealDialog.getByRole('button', { name: /ho salvato il token altrove, chiudi/i }).click()
})
