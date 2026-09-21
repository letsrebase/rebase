import { expect, test } from '@playwright/test'
import { createCustomer, loginAsAdmin } from './helpers'

// The brief this spec was drafted from called `login(page)` with a single
// argument; the real helper (`helpers.ts`) takes `(page, email, password)`, and
// `loginAsAdmin(page)` is the zero-argument convenience every other spec in this
// suite (`crm.spec.ts`, `kanban.spec.ts`, ...) already uses for exactly this case.
test.describe('Documenti', () => {
  test('una tab Documenti compare su un cliente e accetta un caricamento', async ({ page }) => {
    await loginAsAdmin(page)
    const customerName = await createCustomer(page)

    await page.getByText(customerName).click()
    await page.getByRole('tab', { name: 'Documenti' }).click()
    await expect(page.getByText('Nessun documento.')).toBeVisible()

    await page.getByLabel('Carica un documento').setInputFiles({
      name: 'offerta.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.7\nfinto\n'),
    })

    await expect(page.getByText('offerta.pdf')).toBeVisible()
    await expect(page.getByText('v1')).toBeVisible()
  })

  test('un file di tipo non ammesso mostra il messaggio del server', async ({ page }) => {
    await loginAsAdmin(page)
    const customerName = await createCustomer(page)
    await page.getByText(customerName).click()
    await page.getByRole('tab', { name: 'Documenti' }).click()

    await page.getByLabel('Carica un documento').setInputFiles({
      name: 'pagina.html',
      mimeType: 'text/html',
      buffer: Buffer.from('<script>alert(1)</script>'),
    })

    await expect(page.getByRole('alert')).toContainText('tipo di file non ammesso')
  })

  test('una persona non ha una tab Documenti', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto('/app/people')
    const firstPerson = page.getByRole('link').first()
    if (await firstPerson.isVisible()) {
      await firstPerson.click()
      await expect(page.getByRole('tab', { name: 'Documenti' })).toHaveCount(0)
    }
  })
})
