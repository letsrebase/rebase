import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * An admin sets every taxpayer parameter the estimates need from the fiscal form (REB-1070):
 * the INPS facts, the ISA flag, the activity's annex 4 group and the start-up answers. Each is
 * saved, read back by the API and found again after a reload. The spec works on a legal entity
 * of its own and deactivates it again, since the other specs expect one active legal entity.
 */
test('an admin sets the taxpayer parameters from the fiscal form', async ({ page }) => {
  const name = `Parametri ${Date.now()}`
  await loginAsAdmin(page)
  const created = await page.request.post('/api/legal-entities', {
    data: {
      name,
      legal_name: name,
      country: 'IT',
      fiscal_profile: { pack_id: 'it-flat-rate', regime_code: 'RF19' },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  const id = ((await created.json()) as { id: string }).id
  try {
    await page.goto('/app/settings/legal-entities')
    await page.getByRole('tab', { name }).click()
    await expect(page.getByLabel('Regime', { exact: true })).toHaveValue('RF19')

    await page.getByLabel('Cassa INPS').selectOption('artigiani_commercianti')
    await page.getByLabel('Gestione INPS').selectOption('commercianti')
    await page.getByLabel(/prima del 1996/).selectOption('true')
    await page.getByLabel(/Riduzione contributiva/).selectOption('true')
    await page.getByLabel('Soggetto agli ISA').selectOption('false')
    await page
      .getByLabel('Attività (gruppo ATECO)')
      .selectOption('wholesale_and_retail_trade')
    await expect(page.getByText(/Coefficiente di redditività dell’allegato 4: 40%/)).toBeVisible()
    await page.getByLabel('Anno di inizio attività').fill('2024')
    await page.getByLabel(/Nessuna attività prima/).selectOption('true')
    await page.getByLabel(/Non è la prosecuzione/).selectOption('true')
    await page.getByLabel(/Ricavi di chi ti ha preceduto/).selectOption('false')
    await page.getByRole('button', { name: 'Salva' }).last().click()
    await expect(page.getByText('Profilo fiscale salvato')).toBeVisible()

    const read = await page.request.get(`/api/legal-entities/${id}/fiscal-profile`)
    expect(await read.json()).toMatchObject({
      inps_category: 'artigiani_commercianti',
      inps_trade: 'commercianti',
      inps_before_1996: true,
      flat_rate_inps_reduction: true,
      isa: false,
      ateco_group: 'wholesale_and_retail_trade',
      flat_rate_start_year: 2024,
      startup_no_prior_activity: true,
      startup_not_a_continuation: true,
      startup_predecessor_within_limit: false,
      profitability_coefficient: '40.00',
    })

    await page.reload()
    await page.getByRole('tab', { name }).click()
    await expect(page.getByLabel('Gestione INPS')).toHaveValue('commercianti')
    await expect(page.getByLabel('Anno di inizio attività')).toHaveValue('2024')
    await expect(page.getByLabel(/Ricavi di chi ti ha preceduto/)).toHaveValue('false')

    // The ordinary regime keeps the INPS facts and the ISA flag and drops the flat-rate ones.
    await page.getByLabel('Regime', { exact: true }).selectOption('RF01')
    await expect(page.getByLabel('Gestione INPS')).toHaveValue('commercianti')
    await expect(page.getByLabel('Anno di inizio attività')).toHaveCount(0)
    await expect(page.getByLabel('Attività (gruppo ATECO)')).toHaveCount(0)
  } finally {
    const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
  }
})
