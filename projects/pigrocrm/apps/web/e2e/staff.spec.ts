import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * «Correggi il cedolino» (REB-1030): a person accepts a payslip on the staff page, then corrects
 * its gross, and the month shows the corrected figure. The person is written through the API,
 * the payslip and its correction through the screen, so the whole path is the one a person uses:
 * the panel, the person-only route and the month read again.
 */
test('a person corrects a recorded payslip and the month follows', async ({ page }) => {
  await loginAsAdmin(page)
  const entities = (await (await page.request.get('/api/legal-entities')).json()) as {
    id: string
    is_default: boolean
  }[]
  const studio = entities.find((entity) => entity.is_default)
  expect(studio).toBeDefined()
  const name = `Correzione ${Date.now()}`

  // Another spec leaves the space on a profile that has the module off; a person turns it on.
  const on = await page.request.put('/api/settings/modules', { data: { on: ['staff'] } })
  expect(on.status(), await on.text()).toBe(200)

  const day = await page.request.put(`/api/staff/pay-days/${studio!.id}`, {
    data: { pay_day: 27, pay_month_offset: 0 },
  })
  expect(day.status(), await day.text()).toBe(200)
  const created = await page.request.post('/api/staff', {
    data: {
      kind: 'employee',
      legal_entity_id: studio!.id,
      name,
      start_date: '2026-01-01',
      gross_annual_pay: '30000.00',
      monthly_payments: 14,
      part_time_percent: '100',
      employer_inps_rate: '30.00',
      employee_inps_rate: '9.19',
      employer_inail_rate: '1.00',
    },
  })
  expect(created.status(), await created.text()).toBe(201)

  await page.goto('/app/staff?month=2026-03')
  await page.getByText(name, { exact: true }).first().click()
  const panel = page.getByLabel('Persona aperta')

  // Accepted with typed figures: the month is the payslip's from now on.
  await panel.getByLabel('Lordo (€)').fill('2500,00')
  await panel.getByLabel('Netto (€)').fill('1850,50')
  await panel.getByLabel('Ritenute IRPEF (€)').fill('300')
  await panel.getByRole('button', { name: 'Accetta il cedolino' }).click()
  const recorded = panel.getByLabel('Cedolino')
  await expect(recorded.getByText('Registrato')).toBeVisible()
  await expect(recorded).toContainText('2.500,00')

  // Corrected: the form starts from the recorded figures and the panel shows the new ones.
  await panel.getByRole('button', { name: 'Correggi il cedolino' }).click()
  await expect(panel.getByLabel('Lordo (€)')).toHaveValue('2500.00')
  await expect(panel.getByRole('button', { name: 'Salva la correzione' })).toBeDisabled()
  await panel.getByLabel('Lordo (€)').fill('2600,00')
  const [answer] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'PATCH' && /\/payslips\//.test(response.url())),
    panel.getByRole('button', { name: 'Salva la correzione' }).click(),
  ])
  expect(answer.status(), await answer.text()).toBe(200)
  await expect(panel.getByRole('button', { name: 'Correggi il cedolino' })).toBeVisible()
  await expect(recorded).toContainText('2.600,00')
  await expect(recorded).not.toContainText('2.500,00')
  // The month's own gross, read again from the server, is the corrected one.
  await expect(panel.getByLabel('Il mese')).toContainText('2.600,00')
})
