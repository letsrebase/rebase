import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * The Home's tax block draws the tax due items with their amount and state, and a pack date with
 * no item for its tax as before (REB-1095), against the real API: the company Home's band and,
 * with the space's profile read as `solo`, the solo Home's block, on a desktop and on a phone.
 * Whatever the soonest open tax item is over the API is what the Home names first; with none,
 * the pack's lines say «non calcolabile». The images are taken for the pull request.
 */
const SHOTS = '/home/dev/.cache/tmp/REB-1095'

interface Item {
  id: string
  state: string
  due_date: string | null
  source_kind: string
  description: string | null
}

async function soonestOpenTaxItem(page: Page): Promise<Item | null> {
  const found: Item[] = []
  const entity = await page.request.get('/api/legal-entities')
  const entities = (await entity.json()) as { id: string }[]
  const only = entities[0]
  if (!only) return null
  for (const kind of ['tax_balance', 'tax_advance', 'tax_instalment', 'chamber_fee']) {
    const answer = await page.request.get(
      `/api/due-items?legal_entity_id=${only.id}&direction=out&source_kind=${kind}&limit=200`,
    )
    expect(answer.status(), await answer.text()).toBe(200)
    found.push(...((await answer.json()) as { items: Item[] }).items)
  }
  // The Home reads this calendar year, as «Tasse» does.
  const year = `${new Date().getFullYear()}-01-01`
  const open = found.filter((item) => item.due_date !== null && item.due_date >= year)
  open.sort((a, b) => (a.due_date ?? '').localeCompare(b.due_date ?? ''))
  return open[0] ?? null
}

async function shoot(page: Page, name: string): Promise<void> {
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

for (const profile of ['company', 'solo'] as const) {
  test(`the ${profile} Home names the tax items with their amount and state`, async ({ page }) => {
    await loginAsAdmin(page)
    if (profile === 'solo') {
      await page.route('**/api/settings/modules', async (route) => {
        const response = await route.fetch()
        const body = (await response.json()) as Record<string, unknown>
        await route.fulfill({ response, json: { ...body, profile: 'solo' } })
      })
    }
    const soonest = await soonestOpenTaxItem(page)
    for (const [label, size] of [
      ['1440', { width: 1440, height: 900 }],
      ['390', { width: 390, height: 844 }],
    ] as const) {
      await page.setViewportSize(size)
      await page.goto('/app')
      const block = page.getByRole('region', {
        name: profile === 'solo' ? 'Prossime tasse' : 'Prossime scadenze fiscali',
      })
      await expect(block).toBeVisible()
      await expect(block.getByRole('link', { name: 'Apri Tasse' })).toBeVisible()
      if (soonest !== null && soonest.state !== 'settled' && soonest.description !== null) {
        const title = soonest.description.split('. Include ')[0] ?? ''
        await expect(block.getByText(title).first()).toBeVisible()
        await expect(block.getByText(/Da pagare|Previsto|Scaduto/).first()).toBeVisible()
      } else {
        await expect(block.getByText('non calcolabile').first()).toBeVisible()
      }
      await shoot(page, `home-${profile}-${label}`)
    }
  })
}
