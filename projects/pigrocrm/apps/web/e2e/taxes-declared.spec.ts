import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * «Tasse» types the declared figures (REB-1094), against the real API: an administrator types
 * the IRES return of an srl from the tax's panel, the panel reads «Dichiarato» as the one that
 * counts, and a correction replaces the three figures. Desktop and phone renders are saved.
 */
const SHOTS = '/home/dev/.cache/tmp/REB-1094'

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

for (const [label, width, height] of [
  ['1440', 1440, 900],
  ['390', 390, 844],
] as const) {
  test(`the declared figures are typed and corrected from the tax’s panel at ${label}`, async ({ page }) => {
    mkdirSync(SHOTS, { recursive: true })
    const name = `Srl dichiarato ${label} ${Date.now()}`
    await page.setViewportSize({ width, height })
    await loginAsAdmin(page)
    const id = await createSrl(page, name)
    try {
      await page.goto('/app/taxes?tab=taxes&year=2026')
      const section = page.getByRole('region', { name })
      const open = width > 600 ? section.getByRole('row', { name: /IRES/ }) : section.getByRole('button', { name: /IRES/ })
      await open.click()
      const panel = page.getByRole(width > 600 ? 'complementary' : 'dialog', { name: /Imposta aperta/ })
      await panel.getByRole('button', { name: 'Digita il dichiarato' }).click()
      const dialog = page.getByRole('dialog').last()
      await dialog.getByLabel('Imposta dovuta').fill('12.000,00')
      await dialog.getByLabel('Saldo').fill('-150,00')
      await page.screenshot({ path: `${SHOTS}/declared-dialog-${label}.png` })
      await dialog.getByRole('button', { name: 'Salva il dichiarato' }).click()
      await expect(dialog).toHaveCount(0)
      await expect(panel.getByText('Vale: Dichiarato.')).toBeVisible()
      await page.screenshot({ path: `${SHOTS}/declared-panel-${label}.png` })

      await panel.getByRole('button', { name: 'Correggi il dichiarato' }).click()
      await expect(page.getByRole('dialog').last().getByLabel('Imposta dovuta')).toHaveValue('12000.00')
      await page.getByRole('dialog').last().getByLabel('Imposta dovuta').fill('11500,00')
      await page.getByRole('dialog').last().getByRole('button', { name: 'Salva il dichiarato' }).click()
      const read = await page.request.get(`/api/analytics/fiscal?year=2026&legal_entity_id=${id}`)
      await expect
        .poll(async () => {
          const body = (await (await page.request.get(`/api/analytics/fiscal?year=2026&legal_entity_id=${id}`)).json()) as {
            declared: Array<{ tax: string; tax_due: string }>
          }
          return body.declared.find((figure) => figure.tax === 'ires')?.tax_due
        })
        .toBe('11500.00')
      expect(read.ok()).toBe(true)
    } finally {
      const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
      expect(deactivated.status(), await deactivated.text()).toBe(200)
    }
  })
}
