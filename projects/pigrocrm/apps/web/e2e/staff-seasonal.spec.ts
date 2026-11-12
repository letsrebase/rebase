import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * The contract history of a seasonal worker (REB-1090): a person opens «Modifica condizioni» on a
 * seasonal employee, adds the two contracts with their dates and the days of effective work in
 * each tax year, and the server keeps the history whole. The person is written through the API,
 * the history through the screen; the deduction it earns is held by the core tests of the estimate.
 */
test('a person records the contract history of a seasonal worker', async ({ page }) => {
  await loginAsAdmin(page)
  const entities = (await (await page.request.get('/api/legal-entities')).json()) as {
    id: string
    is_default: boolean
  }[]
  const studio = entities.find((entity) => entity.is_default)
  expect(studio).toBeDefined()
  const name = `Stagionale ${Date.now()}`

  const on = await page.request.put('/api/settings/modules', { data: { on: ['staff'] } })
  expect(on.status(), await on.text()).toBe(200)
  const created = await page.request.post('/api/staff', {
    data: {
      kind: 'employee',
      legal_entity_id: studio!.id,
      name,
      start_date: '2025-01-01',
      gross_annual_pay: '20000.00',
      monthly_payments: 13,
      part_time_percent: '100',
      employer_inps_rate: '30.00',
      contract_type: 'seasonal',
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  const id = ((await created.json()) as { id: string }).id

  await page.goto('/app/staff?month=2026-03')
  await page.getByText(name, { exact: true }).first().click()
  await page.getByLabel('Persona aperta').getByRole('button', { name: 'Modifica condizioni' }).click()
  const dialog = page.getByRole('dialog', { name: 'Modifica condizioni' })

  await dialog.getByRole('button', { name: 'Aggiungi un contratto' }).click()
  await dialog.getByLabel('Contratto 1, dal').fill('2025-06-01')
  await dialog.getByLabel('Contratto 1, fino al').fill('2025-09-30')
  await dialog.getByLabel('Giorni lavorati nel 2025').fill('90')
  await dialog.getByRole('button', { name: 'Aggiungi un contratto' }).click()
  await dialog.getByLabel('Contratto 2, dal').fill('2026-06-01')
  await dialog.getByLabel('Contratto 2, fino al').fill('2026-09-30')
  await dialog.getByLabel('Giorni lavorati nel 2026').fill('100')

  const [answer] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'PATCH' && response.url().endsWith(`/api/staff/${id}`)),
    dialog.getByRole('button', { name: 'Salva le condizioni' }).click(),
  ])
  expect(answer.status(), await answer.text()).toBe(200)

  const read = await page.request.get(`/api/staff/${id}`)
  expect(await read.json()).toMatchObject({
    seasonal_contracts: [
      { start_date: '2025-06-01', end_date: '2025-09-30', worked_days: { '2025': 90 } },
      { start_date: '2026-06-01', end_date: '2026-09-30', worked_days: { '2026': 100 } },
    ],
  })
})
