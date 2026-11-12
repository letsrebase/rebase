import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * An ordinary-regime person says from the fiscal form whether the income of the activity is an
 * art or profession or a business (REB-1099), the fact the 20% withholding of art. 25 DPR
 * 600/1973 depends on. The answer is saved, read back by the API and found again after a reload;
 * the forfettario is not asked. The spec works on a legal entity of its own and deactivates it
 * again, since the other specs expect one active legal entity.
 */
test('an ordinary person answers the income kind from the fiscal form', async ({ page }) => {
  const name = `Reddito ${Date.now()}`
  await loginAsAdmin(page)
  const created = await page.request.post('/api/legal-entities', {
    data: {
      name,
      legal_name: name,
      country: 'IT',
      fiscal_profile: { pack_id: 'it-standard', regime_code: 'RF01' },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  const id = ((await created.json()) as { id: string }).id
  try {
    await page.goto('/app/settings/legal-entities')
    await page.getByRole('tab', { name }).click()
    await expect(page.getByLabel('Regime', { exact: true })).toHaveValue('RF01')
    await expect(page.getByLabel('Tipo di reddito')).toHaveValue('')

    await page.getByLabel('Tipo di reddito').selectOption('business')
    await page.getByRole('button', { name: 'Salva' }).last().click()
    await expect(page.getByText('Profilo fiscale salvato')).toBeVisible()

    const read = await page.request.get(`/api/legal-entities/${id}/fiscal-profile`)
    expect(await read.json()).toMatchObject({ income_kind: 'business' })

    await page.reload()
    await page.getByRole('tab', { name }).click()
    await expect(page.getByLabel('Tipo di reddito')).toHaveValue('business')

    await page.getByLabel('Regime', { exact: true }).selectOption('RF19')
    await expect(page.getByLabel('Tipo di reddito')).toHaveCount(0)
  } finally {
    const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
  }
})
