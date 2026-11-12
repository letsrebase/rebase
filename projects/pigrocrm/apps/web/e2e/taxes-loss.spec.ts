import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * «Tasse» types the loss of a past year (REB-1088), against the real API: an administrator types
 * the 2025 loss of an srl from the IRES panel, the server lists it and the panel says what the
 * 2026 estimate did with it, a correction replaces it and «Togli» removes it. Desktop and phone
 * renders are saved.
 */
const SHOTS = '/home/dev/.cache/tmp/REB-1088'

async function createSrl(page: Page, name: string): Promise<string> {
  const created = await page.request.post('/api/legal-entities', {
    data: {
      name,
      legal_name: name,
      country: 'IT',
      city: 'Milano',
      province: 'MI',
      fiscal_profile: { pack_id: 'it-company', regime_code: 'RF01' },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  return ((await created.json()) as { id: string }).id
}

type Losses = { losses: Array<{ tax: string; year: number; amount: string }> }

for (const [label, width, height] of [
  ['1440', 1440, 900],
  ['390', 390, 844],
] as const) {
  test(`a past year’s loss is typed, corrected and removed from the IRES panel at ${label}`, async ({ page }) => {
    mkdirSync(SHOTS, { recursive: true })
    const name = `Srl perdita ${label} ${Date.now()}`
    await page.setViewportSize({ width, height })
    await loginAsAdmin(page)
    const id = await createSrl(page, name)
    const losses = async () =>
      ((await (await page.request.get(`/api/analytics/fiscal?year=2026&legal_entity_id=${id}`)).json()) as Losses).losses
    try {
      await page.goto('/app/taxes?tab=taxes&year=2026')
      const section = page.getByRole('region', { name })
      const open = width > 600 ? section.getByRole('row', { name: /IRES/ }) : section.getByRole('button', { name: /IRES/ })
      await open.click()
      const panel = page.getByRole(width > 600 ? 'complementary' : 'dialog', { name: /Imposta aperta/ })
      await panel.getByRole('button', { name: 'Digita una perdita' }).click()
      const dialog = page.getByRole('dialog', { name: /una perdita: IRES/ })
      await expect(dialog.getByLabel('Anno della perdita')).toHaveValue('2025')
      await dialog.getByLabel('Importo della perdita').fill('50.000,00')
      await page.screenshot({ path: `${SHOTS}/loss-dialog-${label}.png` })
      await dialog.getByRole('button', { name: 'Salva la perdita' }).click()
      await expect(dialog).toHaveCount(0)
      await expect.poll(async () => (await losses()).map((loss) => [loss.tax, loss.year, loss.amount])).toEqual([
        ['ires', 2025, '50000.00'],
      ])
      await expect(panel.getByText(/2025: 50\.000,00/)).toBeVisible()
      await page.screenshot({ path: `${SHOTS}/loss-panel-${label}.png` })

      await panel.getByRole('button', { name: 'Correggi', exact: true }).click()
      const again = page.getByRole('dialog', { name: /Correggi una perdita: IRES/ })
      await expect(again.getByLabel('Importo della perdita')).toHaveValue('50000.00')
      await again.getByLabel('Importo della perdita').fill('40000,00')
      await again.getByRole('button', { name: 'Salva la perdita' }).click()
      await expect.poll(async () => (await losses())[0]?.amount).toBe('40000.00')

      await panel.getByRole('button', { name: 'Togli la perdita del 2025' }).click()
      await expect.poll(async () => (await losses()).length).toBe(0)
    } finally {
      const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
      expect(deactivated.status(), await deactivated.text()).toBe(200)
    }
  })
}
