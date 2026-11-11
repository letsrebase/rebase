import { expect, test, type Page } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * An ordinary-regime person sees the IRPEF and the INPS estimate on «Tasse» (REB-1072): the two
 * lines the pack lists, each opening to what it was computed from, and a profile with no INPS
 * category says so instead of showing a figure. The spec works on legal entities of its own, in an
 * address of Emilia-Romagna, and deactivates them again, since the other specs expect one active
 * legal entity.
 */
async function person(page: Page, name: string, profile: Record<string, unknown>): Promise<string> {
  const created = await page.request.post('/api/legal-entities', {
    data: {
      name,
      legal_name: name,
      country: 'IT',
      city: 'Albinea',
      province: 'RE',
      fiscal_profile: { pack_id: 'it-standard', regime_code: 'RF01', ...profile },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  const made: { id: string } = await created.json()
  return made.id
}

interface Estimate {
  person: { irpef: { amount: string }; inps: { amount: string } }
  readings: { lines: { tax: string; estimate: string | null }[] }
}

test('an ordinary person reads IRPEF and INPS on Tasse and what each is computed from', async ({
  page,
}) => {
  const complete = `Ordinario ${Date.now()}`
  const lacking = `Senza cassa ${Date.now()}`
  await loginAsAdmin(page)
  const ids = [
    await person(page, complete, {
      inps_category: 'gestione_separata',
      inps_covered_elsewhere: true,
    }),
    await person(page, lacking, {}),
  ]
  try {
    // The same payload the page draws, over the wire: no collected invoice yet, so an income of
    // zero, and with it zero IRPEF, zero INPS and no surcharge asked of an address.
    const estimate = await page.request.get(
      `/api/analytics/fiscal?year=2026&legal_entity_id=${ids[0]}`,
    )
    expect(estimate.status(), await estimate.text()).toBe(200)
    const body: Estimate = await estimate.json()
    expect(body.person.irpef.amount).toBe('0.00')
    expect(body.person.inps.amount).toBe('0.00')
    expect(body.readings.lines.map((line) => [line.tax, line.estimate])).toEqual([
      ['irpef', '0.00'],
      ['inps', '0.00'],
    ])

    await page.goto('/app/taxes?year=2026')
    const section = page.getByRole('region', { name: complete })
    await expect(section.getByRole('row', { name: /IRPEF/ })).toBeVisible()
    await section.getByRole('row', { name: /IRPEF/ }).click()
    await expect(page.getByText('Come è calcolata la stima')).toBeVisible()
    await expect(page.getByText('IRPEF lorda', { exact: true })).toBeVisible()
    await expect(page.getByText(/non sono applicate: l.imposta è quindi per eccesso/)).toBeVisible()

    // A profile with no INPS category: no figure, and the sentence names what is missing.
    const other = page.getByRole('region', { name: lacking })
    await expect(other.getByText(/Manca la categoria INPS/).first()).toBeVisible()
  } finally {
    for (const id of ids) {
      const deactivated = await page.request.delete(`/api/legal-entities/${id}`)
      expect(deactivated.status(), await deactivated.text()).toBe(200)
    }
  }
})
