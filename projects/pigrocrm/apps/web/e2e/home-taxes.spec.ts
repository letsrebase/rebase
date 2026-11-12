import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { ensureSpaceHasWork, loginAsAdmin } from './helpers'

/**
 * The Home's tax block draws the tax due items with their amount and state, and a pack date with
 * no item for its tax as before (REB-1095): the company Home's band and the solo Home's block, on
 * a desktop and on a phone. The e2e space has a flat-rate entity with no work, so the real API
 * answers the pack's dates and no tax item: that is the first case, «non calcolabile» under each
 * date. The second answers the due items with an advance and an overdue balance, as the server
 * would once an estimate exists, and the Home names them with their amounts and states. The
 * images are taken for the pull request.
 */
const SHOTS = '/home/dev/.cache/tmp/REB-1095'

type Profile = 'company' | 'solo'
const SIZES = [
  ['1440', { width: 1440, height: 900 }],
  ['390', { width: 390, height: 844 }],
] as const

async function openHome(page: Page, profile: Profile): Promise<void> {
  await loginAsAdmin(page)
  // A space with no work opens on «Primi passi», not on the Home.
  await ensureSpaceHasWork(page)
  // The e2e space skipped its onboarding: both Homes are reached by reading the space's profile as
  // the one chosen, over the same real data.
  await page.route('**/api/settings/modules', async (route) => {
    const response = await route.fetch()
    const body = (await response.json()) as Record<string, unknown>
    await route.fulfill({ response, json: { ...body, profile, onboarding: 'done' } })
  })
}

async function shoot(page: Page, name: string): Promise<void> {
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${name}.png` })
}

/** Reads the open tax items of the Home over a mocked answer for the tax source kinds. */
async function answerTaxItems(page: Page, legalEntityId: string): Promise<void> {
  const year = new Date().getFullYear()
  const iso = (date: Date) =>
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  const yesterday = new Date()
  yesterday.setDate(yesterday.getDate() - 1)
  // The pack's next date, so the advance sits on a day the calendar names too.
  const calendar = await page.request.get(`/api/analytics/fiscal/calendar?year=${year}`)
  expect(calendar.status(), await calendar.text()).toBe(200)
  const dates = ((await calendar.json()) as { dates: { due_date: string }[] }).dates
  const next = dates.map((entry) => entry.due_date).find((date) => date >= iso(new Date()))
  expect(next, 'the pack has a date left this year').toBeDefined()
  const item = (id: string, kind: string, due: string, amount: string, description: string) => ({
    id,
    legal_entity_id: legalEntityId,
    direction: 'out',
    state: 'expected',
    counterparty_type: 'tax_office',
    organization_id: null,
    organization_name: null,
    staff_member_id: null,
    tax: 'substitute_tax',
    amount,
    currency: 'EUR',
    settled: '0.00',
    remaining: amount,
    description,
    source_kind: kind,
    source_id: id,
    source_rule: null,
    own_date: due,
    due_date: due,
    due_date_estimated: false,
    rule: {
      kind: 'fixed',
      depends_on_id: null,
      offset_days: 0,
      day_of_month: null,
      not_after: null,
    },
    dropped_reason: null,
    carry_to_confirm: false,
    created_at: `${year}-01-01T00:00:00Z`,
    updated_at: `${year}-01-01T00:00:00Z`,
  })
  const items = [
    item(
      'e2e-balance',
      'tax_balance',
      iso(yesterday),
      '1250.00',
      `Saldo imposta sostitutiva ${year - 1}`,
    ),
    item(
      'e2e-advance',
      'tax_advance',
      next ?? '',
      '700.00',
      `Secondo acconto imposta sostitutiva ${year}`,
    ),
  ]
  await page.route('**/api/due-items?*', async (route) => {
    const kind = new URL(route.request().url()).searchParams.get('source_kind')
    const state = new URL(route.request().url()).searchParams.get('state')
    const own = state === null ? items.filter((entry) => entry.source_kind === kind) : []
    await route.fulfill({ json: { items: own, next_cursor: null } })
  })
}

for (const profile of ['company', 'solo'] as const) {
  const heading = profile === 'solo' ? 'Prossime tasse' : 'Prossime scadenze fiscali'

  test(`the ${profile} Home keeps the pack's dates when no tax item exists`, async ({ page }) => {
    await openHome(page, profile)
    for (const [label, size] of SIZES) {
      await page.setViewportSize(size)
      await page.goto('/app')
      const block = page.getByRole('region', { name: heading })
      await expect(block).toBeVisible()
      await expect(block.getByText('non calcolabile').first()).toBeVisible()
      await expect(block.getByRole('link', { name: 'Apri Tasse' })).toBeVisible()
      await block.scrollIntoViewIfNeeded()
      await shoot(page, `home-${profile}-dates-${label}`)
    }
  })

  test(`the ${profile} Home names the tax items with their amount and state`, async ({ page }) => {
    await openHome(page, profile)
    const entities = (await (await page.request.get('/api/legal-entities')).json()) as {
      id: string
    }[]
    const entity = entities[0]
    expect(entity).toBeDefined()
    await answerTaxItems(page, entity?.id ?? '')
    for (const [label, size] of SIZES) {
      await page.setViewportSize(size)
      await page.goto('/app')
      const block = page.getByRole('region', { name: heading })
      await expect(block).toBeVisible()
      // The overdue balance comes first, then the advance with its own amount; an item covers
      // each day, so no pack line is left to say «non calcolabile».
      await expect(block.getByText(/^Saldo imposta sostitutiva/)).toBeVisible()
      await expect(block.getByText('Scaduto')).toBeVisible()
      await expect(block.getByText(/1\.250,00/)).toBeVisible()
      await expect(block.getByText(/^Secondo acconto imposta sostitutiva/)).toBeVisible()
      await expect(block.getByText('Da pagare')).toBeVisible()
      await expect(block.getByText(/700,00/)).toBeVisible()
      await expect(block.getByText('non calcolabile')).toHaveCount(0)
      await block.scrollIntoViewIfNeeded()
      await shoot(page, `home-${profile}-items-${label}`)
    }
  })
}
