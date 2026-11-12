import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * Apprentices, workers on a training contract and disabled workers (REB-1091): a person opens
 * «Modifica condizioni» on an employee, states the contract type and whether the person is
 * disabled, and the server keeps both. The person is written through the API, the two facts
 * through the screen, so the path is the one a person uses; the IRAP figure they change is held
 * by the core tests of the estimate.
 */
test('a person states that an employee is an apprentice and disabled', async ({ page }) => {
  await loginAsAdmin(page)
  const entities = (await (await page.request.get('/api/legal-entities')).json()) as {
    id: string
    is_default: boolean
  }[]
  const studio = entities.find((entity) => entity.is_default)
  expect(studio).toBeDefined()
  const name = `Apprendista ${Date.now()}`

  const on = await page.request.put('/api/settings/modules', { data: { on: ['staff'] } })
  expect(on.status(), await on.text()).toBe(200)
  const created = await page.request.post('/api/staff', {
    data: {
      kind: 'employee',
      legal_entity_id: studio!.id,
      name,
      start_date: '2026-01-01',
      gross_annual_pay: '20000.00',
      monthly_payments: 13,
      part_time_percent: '100',
      employer_inps_rate: '30.00',
      contract_type: 'fixed_term',
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  const id = ((await created.json()) as { id: string }).id

  await page.goto('/app/staff?month=2026-03')
  await page.getByText(name, { exact: true }).first().click()
  await page.getByLabel('Persona aperta').getByRole('button', { name: 'Modifica condizioni' }).click()

  const dialog = page.getByRole('dialog', { name: 'Modifica condizioni' })
  await dialog.getByLabel('Tipo di contratto').click()
  await page.getByRole('option', { name: 'Apprendistato' }).click()
  await dialog.getByLabel('Lavoratore disabile').click()
  await page.getByRole('option', { name: 'Sì' }).click()
  const [answer] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'PATCH' && response.url().endsWith(`/api/staff/${id}`)),
    dialog.getByRole('button', { name: 'Salva le condizioni' }).click(),
  ])
  expect(answer.status(), await answer.text()).toBe(200)

  const read = await page.request.get(`/api/staff/${id}`)
  expect(await read.json()).toMatchObject({ contract_type: 'apprentice', disabled_worker: true })
})
