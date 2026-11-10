import { mkdirSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * A payslip from the inbox (REB-1045): a PDF dropped in the inbox with a proposal makes the person's
 * month «Ricevuto»; a person accepts it in the panel and the month is the payslip's; closing the month
 * makes it «Chiuso» and refuses a correction. The renders at 390 and 1440 are written under
 * `/home/dev/.cache/tmp/REB-1045/`.
 */
const SHOTS = '/home/dev/.cache/tmp/REB-1045'

test('a payslip read from the inbox is received, accepted by a person, and the month is closed', async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true })
  await loginAsAdmin(page)
  const entities = (await (await page.request.get('/api/legal-entities')).json()) as { id: string; is_default: boolean }[]
  const studio = entities.find((entity) => entity.is_default)
  expect(studio).toBeDefined()
  const name = `Cedolino ${Date.now()}`

  const on = await page.request.put('/api/settings/modules', { data: { on: ['staff'] } })
  expect(on.status(), await on.text()).toBe(200)
  const day = await page.request.put(`/api/staff/pay-days/${studio!.id}`, { data: { pay_day: 27, pay_month_offset: 0 } })
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
  const member = ((await created.json()) as { id: string }).id

  const upload = await page.request.post('/api/inbox/upload', {
    multipart: {
      file: { name: 'cedolino.pdf', mimeType: 'application/pdf', buffer: Buffer.from(`%PDF-1.7\n1 0 obj\n<<>>\nendobj\n% ${Date.now()}\n`) },
    },
  })
  expect(upload.status(), await upload.text()).toBe(201)
  const item = ((await upload.json()) as { items: { id: string }[] }).items[0]!
  const proposed = await page.request.post('/api/proposals', {
    data: {
      inbox_item_id: item.id,
      target_type: 'payslip',
      excerpt: 'Netto in busta 1.607,15',
      confidence: '0.9',
      proposed_fields: { staff_member_id: member, period: '2026-05-01', gross: '2142.86', net: '1607.15', withholding: '321.43' },
    },
  })
  expect(proposed.status(), await proposed.text()).toBe(201)

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/app/staff?month=2026-05')
  await page.getByText(name, { exact: true }).first().click()
  const panel = page.getByLabel('Persona aperta')
  await expect(panel.getByText('Ricevuto')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Rifiuta…' })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Apri il PDF' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/staff-received-1440.png`, fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: `${SHOTS}/staff-received-390.png`, fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  await panel.getByRole('button', { name: 'Accetta il cedolino' }).click()
  await expect(panel.getByLabel('Cedolino').getByText('Registrato')).toBeVisible()
  await expect(panel.getByLabel('Cedolino')).toContainText('2.142,86')
  await expect(panel.getByRole('button', { name: 'Apri il PDF' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/staff-accepted-1440.png`, fullPage: true })

  await page.getByRole('button', { name: 'Chiudi il mese' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Chiudi il mese di maggio 2026')
  await dialog.getByRole('button', { name: 'Chiudi il mese' }).click()
  await expect(page.getByText(/Chiuso il .* da /).first()).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Correggi il cedolino' })).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/staff-closed-1440.png`, fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: `${SHOTS}/staff-closed-390.png`, fullPage: true })

  const refused = await page.request.post(`/api/staff/${member}/payslips`, {
    data: { period: '2026-05-01', gross: '1.00', net: '1.00', withholding: '0' },
  })
  expect(refused.status()).toBe(409)
})
