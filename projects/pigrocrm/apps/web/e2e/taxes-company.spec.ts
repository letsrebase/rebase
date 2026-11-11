import { expect, test, type Page } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * An srl's «Tasse» draws an IRES and an IRAP estimate with what each was computed from
 * (REB-1073): a Milan srl with no work in the year has a profit of zero, an IRES of zero and an
 * IRAP of zero at Lombardia's 3.90%, while one with no address has no IRAP figure and the page
 * says which address is missing. The spec works on legal entities of its own and deactivates
 * them again, since the other specs expect one active legal entity.
 */
async function createSrl(page: Page, name: string, address: object): Promise<string> {
  const created = await page.request.post('/api/legal-entities', {
    data: {
      name,
      legal_name: name,
      country: 'IT',
      ...address,
      fiscal_profile: { pack_id: 'it-company', regime_code: 'RF01' },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  return ((await created.json()) as { id: string }).id
}

test('an srl’s Tasse page shows its IRES and IRAP estimates', async ({ page }) => {
  const stamp = Date.now()
  const milan = `Srl Milano ${stamp}`
  const nowhere = `Srl senza sede ${stamp}`
  await loginAsAdmin(page)
  const ids = [
    await createSrl(page, milan, { city: 'Milano', province: 'MI' }),
    await createSrl(page, nowhere, {}),
  ]
  try {
    await page.goto('/app/taxes?tab=taxes&year=2026')
    const section = page.getByRole('region', { name: milan })
    const ires = section.getByRole('row', { name: /IRES/ })
    const irap = section.getByRole('row', { name: /IRAP/ })
    await expect(ires).toContainText('0,00')
    await expect(irap).toContainText('0,00')
    await expect(section.getByText('non calcolabile')).toHaveCount(0)

    await irap.click()
    const panel = page.getByRole('complementary', { name: 'Imposta aperta' })
    await expect(panel.getByText('Come è calcolata la stima')).toBeVisible()
    await expect(panel.getByText('3,90 %')).toBeVisible()
    await expect(panel.getByText(/compensi degli amministratori restano nella base/)).toBeVisible()

    const without = page.getByRole('region', { name: nowhere })
    await expect(without.getByText(/Manca l.indirizzo della sede/)).toBeVisible()
    await expect(without.getByRole('row', { name: /IRAP/ })).toContainText('non calcolabile')

    // The same estimate over the API: the page adds nothing of its own.
    const api = await page.request.get(`/api/analytics/fiscal?year=2026&legal_entity_id=${ids[0]}`)
    const body = (await api.json()) as { company: { irap: { rate: string; region: string } } }
    expect(body.company.irap).toMatchObject({ rate: '3.90', region: '03' })
  } finally {
    for (const id of ids) {
      const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
      expect(deactivated.status(), await deactivated.text()).toBe(200)
    }
  }
})
