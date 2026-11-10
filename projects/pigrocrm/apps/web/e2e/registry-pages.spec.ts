import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * «Clienti» and «Fornitori» are a page each (REB-1053, which reversed the single «Anagrafiche»
 * page of REB-996), and «Collaboratori» is the name of the engagements entry and page. Each
 * sidebar entry opens its own page, and every old address lands on the one it meant.
 */
test.describe('«Clienti», «Fornitori» and «Collaboratori»', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page)
    // «Fornitori» is the purchases module's page and «Collaboratori» is off by default: an earlier
    // spec may have left the space on a profile without either, and a person turns them on.
    const on = await page.request.put('/api/settings/modules', { data: { on: ['purchases', 'collaborators'] } })
    expect(on.status(), await on.text()).toBe(200)
  })

  test('each sidebar entry opens a page of its own, with no tab bar between them', async ({ page }) => {
    const sidebar = page.getByRole('navigation', { name: 'Navigazione principale' })

    await sidebar.getByRole('link', { name: 'Clienti' }).click()
    await expect(page).toHaveURL(/\/app\/customers$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Clienti' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Nuovo cliente' }).first()).toBeVisible()
    await expect(page.getByRole('tablist')).toHaveCount(0)
    await expect(sidebar.getByRole('link', { name: 'Clienti' })).toHaveAttribute('aria-current', 'page')

    await sidebar.getByRole('link', { name: 'Fornitori' }).click()
    await expect(page).toHaveURL(/\/app\/suppliers$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Fornitori' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Nuovo fornitore' }).first()).toBeVisible()
    await expect(page.getByRole('tablist')).toHaveCount(0)
    await expect(sidebar.getByRole('link', { name: 'Fornitori' })).toHaveAttribute('aria-current', 'page')
    await expect(sidebar.getByRole('link', { name: 'Clienti' })).not.toHaveAttribute('aria-current', 'page')
  })

  test('«Nuovo fornitore» creates a supplier, who is on «Fornitori» and not on «Clienti»', async ({ page }) => {
    const name = `Fornitore ${Date.now()}`
    await page.goto('/app/suppliers')
    // An empty list says «Nuovo fornitore» twice, in the header and under its sentence: the header's.
    await page.getByRole('button', { name: 'Nuovo fornitore' }).first().click()
    await page.getByLabel('Ragione sociale').fill(name)
    await page.getByRole('button', { name: 'Salva' }).click()
    await expect(page.getByText(name)).toBeVisible()

    await page.goto('/app/customers')
    await page.getByPlaceholder(/cerca per ragione sociale/i).fill(name)
    await expect(page.getByText(/nessun risultato/i)).toBeVisible()
  })

  test('an old /app/organizations address lands on the page it meant, keeping the search term', async ({ page }) => {
    await page.goto('/app/organizations')
    await expect(page).toHaveURL(/\/app\/customers$/)
    await page.goto('/app/organizations?role=supplier')
    await expect(page).toHaveURL(/\/app\/suppliers$/)
    await page.goto('/app/organizations?role=any')
    await expect(page).toHaveURL(/\/app\/customers$/)
    await page.goto('/app/organizations?role=supplier&search=acme')
    await expect(page).toHaveURL(/\/app\/suppliers\?search=acme$/)
    await expect(page.getByPlaceholder(/cerca per ragione sociale/i)).toHaveValue('acme')
    await page.goto('/app/clienti')
    await expect(page).toHaveURL(/\/app\/customers$/)
  })

  test('the engagements entry and page are «Collaboratori», at the same address', async ({ page }) => {
    const sidebar = page.getByRole('navigation', { name: 'Navigazione principale' })
    await expect(sidebar.getByRole('link', { name: 'Incarichi' })).toHaveCount(0)
    await sidebar.getByRole('link', { name: 'Collaboratori' }).click()
    await expect(page).toHaveURL(/\/app\/engagements/)
    await expect(page.getByRole('heading', { level: 1, name: 'Collaboratori' })).toBeVisible()
  })
})
