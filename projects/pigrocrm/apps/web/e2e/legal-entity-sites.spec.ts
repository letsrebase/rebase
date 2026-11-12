import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * An srl with people in two regions splits its IRAP between them (REB-1092, art. 4 c. 2
 * D.Lgs. 446/1997). A Milan srl and two employees are set up through the API; an administrator
 * adds a Veneto site on «Impostazioni → Aziende», moves one employee there through the staff form,
 * and the company taxes page draws the split. The srl is deactivated again at the end, since the
 * other specs expect one active legal entity.
 */
test('a site in another region splits the srl’s IRAP between the two', async ({ page }) => {
  // Three screens and a dozen requests on a shared box: the default 30 seconds is the first to go.
  test.setTimeout(150_000)
  const stamp = Date.now()
  const srl = `Srl Sedi ${stamp}`
  const away = `Alba Veneta ${stamp}`
  const home = `Bruno Milanese ${stamp}`
  await loginAsAdmin(page)

  // Another spec leaves the space on a profile that has the module off; a person turns it on.
  const on = await page.request.put('/api/settings/modules', { data: { on: ['staff'] } })
  expect(on.status(), await on.text()).toBe(200)

  const created = await page.request.post('/api/legal-entities', {
    data: {
      name: srl,
      legal_name: srl,
      country: 'IT',
      city: 'Milano',
      province: 'MI',
      // An activity no regional list singles out, so every region answers its ordinary rate.
      fiscal_profile: {
        pack_id: 'it-company',
        regime_code: 'RF01',
        ateco_2007_code: '70.22.09',
        ateco_2025_code: '70.22.09',
      },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  const id = ((await created.json()) as { id: string }).id

  try {
    const employee = (name: string, extra: object) =>
      page.request.post('/api/staff', {
        data: {
          kind: 'employee',
          legal_entity_id: id,
          name,
          start_date: '2026-01-01',
          gross_annual_pay: '30000.00',
          monthly_payments: 13,
          part_time_percent: '100',
          employer_inps_rate: '30.00',
          employee_inps_rate: '9.19',
          employer_inail_rate: '1.00',
          ...extra,
        },
      })
    const first = await employee(away, {})
    expect(first.status(), await first.text()).toBe(201)
    const second = await employee(home, { at_registered_office: true })
    expect(second.status(), await second.text()).toBe(201)

    // The site, added through the screen.
    await page.goto('/app/settings/legal-entities')
    await page.getByRole('tab', { name: new RegExp(srl) }).click()
    const sites = page.getByRole('region', { name: 'Sedi' })
    await expect(sites.getByText('Nessuna sede oltre a quella legale.')).toBeVisible()
    await sites.getByRole('button', { name: 'Aggiungi una sede' }).click()
    await sites.getByLabel('Regione *').click()
    await page.getByRole('option', { name: 'Veneto' }).click()
    await sites.getByLabel('Comune').fill('Padova')
    await sites.getByLabel('Indirizzo').fill('Via Roma 3')
    await sites.getByRole('button', { name: 'Aggiungi' }).click()
    await expect(sites.getByText('Padova, Via Roma 3 · Nessuna persona')).toBeVisible()

    // A person moved to it, through the staff form.
    await page.goto('/app/staff?month=2026-03')
    await page.getByRole('combobox', { name: 'Azienda' }).click()
    await page.getByRole('option', { name: srl }).click()
    await page.getByText(away, { exact: true }).first().click()
    const panel = page.getByLabel('Persona aperta')
    await panel.getByRole('button', { name: 'Modifica condizioni' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Sede di lavoro').click()
    await page.getByRole('option', { name: 'Padova, Via Roma 3' }).click()
    const [saved] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === 'PATCH' && /\/api\/staff\//.test(response.url())),
      dialog.getByRole('button', { name: 'Salva le condizioni' }).click(),
    ])
    expect(saved.status(), await saved.text()).toBe(200)
    await expect(panel.getByText('Sede di lavoro')).toBeVisible()
    await expect(panel).toContainText('Padova, Via Roma 3')

    // The site now counts its person.
    await page.goto('/app/settings/legal-entities')
    await page.getByRole('tab', { name: new RegExp(srl) }).click()
    await expect(page.getByRole('region', { name: 'Sedi' }).getByText('Padova, Via Roma 3 · 1 persona')).toBeVisible()

    // The taxes page draws the split, not a rate.
    await page.goto('/app/taxes?tab=taxes&year=2026')
    // One srl on the page has no heading of its own, and its section is named «Tasse»; with
    // several each carries its name. Either way the page has exactly one of the two.
    const section = page
      .getByRole('region', { name: srl })
      .or(page.getByRole('region', { name: 'Tasse', exact: true }))
    await expect(section).toBeVisible()
    await section.getByRole('row', { name: /IRAP/ }).click()
    const detail = page.getByRole('complementary', { name: 'Imposta aperta' })
    await expect(detail.getByText(/Ripartita su 2 regioni/)).toBeVisible()
    const table = detail.getByRole('table')
    await expect(table.getByRole('row', { name: /Veneto/ })).toBeVisible()
    await expect(table.getByRole('row', { name: /Lombardia/ })).toBeVisible()

    // The same split over the API: the page adds nothing of its own.
    const estimate = await page.request.get(`/api/analytics/fiscal?year=2026&legal_entity_id=${id}`)
    const body = (await estimate.json()) as { company: { irap: { rate: string | null; amount: string | null; regions: { name: string }[] } } }
    expect(body.company.irap.rate).toBeNull()
    expect(body.company.irap.amount).not.toBeNull()
    expect(body.company.irap.regions.map((part) => part.name).sort()).toEqual(['Lombardia', 'Veneto'])
  } finally {
    const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
  }
})
