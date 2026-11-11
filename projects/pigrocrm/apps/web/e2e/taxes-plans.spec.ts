import { expect, test, type Locator, type Page } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * «Tasse» acts on plans and method (REB-1079), against the real API: an srl's notice plan is
 * entered, changed and deleted from «Rate», on a desktop and on a phone, and the advance method
 * of a tax year is chosen from the tax's panel and read back. The spec works on a legal entity of
 * its own and deactivates it again, since the other specs expect one active legal entity.
 */
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

async function enterNotice(page: Page, section: Locator, label: string): Promise<void> {
  await section.getByRole('button', { name: 'Nuovo piano' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Avviso', { exact: true }).fill(label)
  await dialog.getByLabel(/Importo dell’avviso/).fill('1.200,00')
  await dialog.getByLabel('Scadenza della rata 1').fill('2027-03-16')
  await dialog.getByLabel('Interessi della rata 1').fill('10,00')
  await dialog.getByRole('button', { name: 'Aggiungi una rata' }).click()
  await dialog.getByLabel('Scadenza della rata 2').fill('2027-04-16')
  await dialog.getByRole('button', { name: 'Registra il piano' }).click()
  await expect(dialog).toHaveCount(0)
}

test('a notice plan is entered, changed and deleted from Rate', async ({ page }) => {
  const stamp = Date.now()
  const name = `Srl rate ${stamp}`
  const label = `Avviso bonario ${stamp}`
  await loginAsAdmin(page)
  const id = await createSrl(page, name)
  try {
    await page.goto('/app/taxes?tab=plans&year=2026')
    const section = page.getByRole('region', { name })
    await expect(section.getByText(/Qui compaiono i piani di rate/)).toBeVisible()
    await enterNotice(page, section, label)

    const row = section.getByRole('row', { name: new RegExp(label) })
    await expect(row).toContainText('1.200,00')
    await expect(row).toContainText('16/03/2027')
    // The same plan over the API: two instalments, the interest the page typed.
    const plans = await page.request.get(`/api/analytics/fiscal/plans?legal_entity_id=${id}`)
    const body = (await plans.json()) as { notice_plans: Array<{ label: string; instalments: unknown[] }> }
    expect(body.notice_plans).toHaveLength(1)
    expect(body.notice_plans[0]?.instalments).toHaveLength(2)

    await row.click()
    const panel = page.getByRole('complementary', { name: 'Piano aperto' })
    await expect(panel.getByText(/interessi 10,00/)).toBeVisible()
    await panel.getByRole('button', { name: 'Modifica' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Avviso', { exact: true }).fill(`${label} bis`)
    await dialog.getByRole('button', { name: 'Salva le modifiche' }).click()
    await expect(dialog).toHaveCount(0)
    await expect(section.getByRole('row', { name: new RegExp(`${label} bis`) })).toBeVisible()

    await panel.getByRole('button', { name: 'Elimina' }).click()
    await panel.getByRole('button', { name: 'Elimina il piano' }).click()
    await expect(section.getByText(/Qui compaiono i piani di rate/)).toBeVisible()
  } finally {
    const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
  }
})

test('on a phone Rate is a column of cards and a plan opens full screen', async ({ page }) => {
  const stamp = Date.now()
  const name = `Srl rate telefono ${stamp}`
  const label = `Cartella ${stamp}`
  await page.setViewportSize({ width: 390, height: 844 })
  await loginAsAdmin(page)
  const id = await createSrl(page, name)
  try {
    await page.goto('/app/taxes?tab=plans&year=2026')
    const section = page.getByRole('region', { name })
    await enterNotice(page, section, label)
    await expect(section.getByRole('table')).toHaveCount(0)
    const card = section.getByRole('button', { name: new RegExp(label) })
    await expect(card).toContainText('Importo senza interessi')
    await card.click()
    const sheet = page.getByRole('dialog', { name: 'Piano aperto' })
    await expect(sheet.getByRole('button', { name: 'Modifica' })).toBeVisible()
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(overflow).toBe(false)
  } finally {
    const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
  }
})

test('the advance method of a tax year is chosen from the tax’s panel and read back', async ({ page }) => {
  const name = `Srl metodo ${Date.now()}`
  await loginAsAdmin(page)
  const id = await createSrl(page, name)
  try {
    await page.goto('/app/taxes?tab=taxes&year=2026')
    const section = page.getByRole('region', { name })
    await section.getByRole('row', { name: /IRES/ }).click()
    const panel = page.getByRole('complementary', { name: 'Imposta aperta' })
    await expect(panel.getByText('Storico')).toBeVisible()
    await panel.getByRole('button', { name: 'Cambia il metodo dell’acconto' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('combobox', { name: 'Metodo' }).click()
    await page.getByRole('option', { name: 'Previsionale' }).click()
    await dialog.getByRole('button', { name: 'Salva il metodo' }).click()
    // A pending report of what could not be written keeps the dialog open: close it either way.
    const close = dialog.getByRole('button', { name: 'Chiudi' })
    if (await close.isVisible().catch(() => false)) await close.click()
    await expect(dialog).toHaveCount(0)
    await expect(panel.getByText('Previsionale')).toBeVisible()
    const read = await page.request.get(`/api/analytics/fiscal?year=2026&legal_entity_id=${id}`)
    expect(((await read.json()) as { advance_method: string }).advance_method).toBe('forecast')
  } finally {
    const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
  }
})

test('a date’s items are grouped in an F24 and the group is dissolved again', async ({ page }) => {
  const stamp = Date.now()
  const name = `Srl f24 ${stamp}`
  await loginAsAdmin(page)
  const id = await createSrl(page, name)
  try {
    const plan = await page.request.post(`/api/analytics/fiscal/notice-plans?legal_entity_id=${id}`, {
      data: {
        label: `Avviso ${stamp}`,
        tax: 'ires',
        total: '1200.00',
        instalments: [{ due_date: '2027-03-16', interest: '10.00' }],
      },
    })
    expect(plan.status(), await plan.text()).toBe(201)
    await page.goto('/app/taxes?tab=dates&year=2027')
    const section = page.getByRole('region', { name })
    const row = section.getByRole('row', { name: /16\/03\/2027/ })
    await expect(row).toContainText('F24 da creare')
    await row.click()
    const panel = page.getByRole('complementary', { name: 'Scadenza aperta' })
    await panel.getByRole('button', { name: 'Raggruppa in un F24' }).click()
    await expect(row).toContainText('F24 creato')
    await expect(panel.getByText(/Netto dell’F24/)).toBeVisible()
    await expect(panel.getByText(/codice da verificare|Codice/).first()).toBeVisible()
    const groups = await page.request.get(`/api/f24-groups?legal_entity_id=${id}`)
    expect(((await groups.json()) as { items: unknown[] }).items).toHaveLength(1)
    await panel.getByRole('button', { name: 'Sciogli l’F24' }).click()
    await panel.getByRole('button', { name: 'Sciogli il gruppo' }).click()
    await expect(row).toContainText('F24 da creare')
  } finally {
    const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
  }
})
