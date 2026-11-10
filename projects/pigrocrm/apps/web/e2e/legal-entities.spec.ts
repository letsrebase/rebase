import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { login, loginAsAdmin } from './helpers'

/**
 * A member scoped to one legal entity sees that legal entity and nothing else (REB-635, spec
 * 2026-10-03 §1.11, §4, §5). The stack runs the API as the application role
 * (`scripts/e2e-env.sh`), so what B does not see here is what Postgres refuses to show,
 * not what a `WHERE` left out.
 *
 * A, the space's admin, opens a second legal entity and one customer under each; B is created
 * and then scoped from the Team panel, through the UI this card adds. B signs in on a
 * second browser context, so A's session stays alive for the clean-up at the end: the
 * second legal entity is deactivated again, since another spec's «Analisi › Fiscale» expects
 * one active legal entity and the files of this suite share the stack.
 */

const STAMP = Date.now()
const LTD = `Scope Ltd ${STAMP}`
const B_EMAIL = `scoped-${STAMP}@pigro.it`
const B_PASSWORD = 'collabora123'

async function createLegalEntity(request: APIRequestContext): Promise<string> {
  const created = await request.post('/api/legal-entities', {
    data: {
      name: LTD,
      legal_name: LTD,
      country: 'GB',
      // A foreign company: no Italian regime, a VAT rate and no natura, the body
      // «Nuova azienda» sends for «Estero» (REB-632).
      fiscal_profile: {
        pack_id: 'non-it',
        regime_code: null,
        default_vat_rate: '20.00',
        default_vat_nature: null,
        legal_reference: null,
        apply_stamp_duty: false,
        profitability_coefficient: null,
        substitute_tax_rate: null,
        social_security_rate: null,
      },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  return ((await created.json()) as { id: string }).id
}

async function createOrganization(
  request: APIRequestContext,
  name: string,
  legalEntityId: string,
): Promise<string> {
  const created = await request.post('/api/organizations', {
    data: { legal_name: name, legal_entity_id: legalEntityId },
  })
  expect(created.status(), await created.text()).toBe(201)
  return ((await created.json()) as { id: string }).id
}

async function scopeFromTheTeamPanel(page: Page, name: string, keep: string): Promise<void> {
  await page.goto('/app/settings/users')
  const row = page.getByRole('row', { name: new RegExp(name) })
  await expect(row).toContainText('Tutte')
  await row.getByRole('button', { name: `Azioni per ${name}` }).click()
  await page.getByRole('menuitem', { name: 'Aziende…' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText(`Aziende di ${name}`)
  // Every legal entity but `keep` unchecked: a stack kept alive between runs may hold more
  // active legal entities than the two this spec makes, and B must end up with exactly one.
  for (const box of await dialog.getByRole('checkbox').all()) {
    const id = await box.getAttribute('id')
    const label = id ? await dialog.locator(`label[for="${id}"]`).textContent() : null
    if (label?.trim() !== keep && (await box.getAttribute('aria-checked')) === 'true') {
      await box.click()
    }
  }
  await dialog.getByRole('button', { name: 'Salva' }).click()
  await expect(dialog).toBeHidden()
  await expect(row).toContainText(LTD)
}

test('a member scoped to one azienda sees it pinned, lists its rows alone and gets a 404 on the rest', async ({
  page,
  browser,
}) => {
  await loginAsAdmin(page)
  const legal_entities = (await (await page.request.get('/api/legal-entities')).json()) as {
    id: string
    name: string
    is_default: boolean
  }[]
  const studio = legal_entities.find((a) => a.is_default)
  expect(studio).toBeDefined()
  const ltdId = await createLegalEntity(page.request)
  const studioOrganization = await createOrganization(page.request, `Studio Cliente ${STAMP}`, studio!.id)
  const ltdOrganization = await createOrganization(page.request, `Ltd Cliente ${STAMP}`, ltdId)

  // B, created by A with a password (no mail leaves the e2e stack), then scoped to
  // «Scope Ltd» from the Team panel: the row said «Tutte», it says the legal entity now.
  const created = await page.request.post('/api/users', {
    data: { email: B_EMAIL, password: B_PASSWORD, name: `Bea ${STAMP}`, role: 'collaborator' },
  })
  expect(created.status(), await created.text()).toBe(201)
  await scopeFromTheTeamPanel(page, `Bea ${STAMP}`, LTD)

  const context = await browser.newContext()
  const bPage = await context.newPage()
  try {
    await login(bPage, B_EMAIL, B_PASSWORD)
    // The sidebar: the legal entity's name where the selector would be, and no selector.
    await expect(bPage.getByLabel('Azienda')).toContainText(LTD)
    await expect(bPage.getByRole('combobox', { name: 'Azienda' })).toHaveCount(0)
    // The organizations list: B's azienda's row and not A's.
    await bPage.goto('/app/organizations')
    await expect(bPage.getByText(`Ltd Cliente ${STAMP}`)).toBeVisible()
    await expect(bPage.getByText(`Studio Cliente ${STAMP}`)).toHaveCount(0)
    // By id: the other legal entity's row does not exist for B, and the one B may see does.
    expect((await bPage.request.get(`/api/organizations/${studioOrganization}`)).status()).toBe(404)
    expect((await bPage.request.get(`/api/organizations/${ltdOrganization}`)).status()).toBe(200)
    expect((await bPage.request.get(`/api/legal-entities/${studio!.id}`)).status()).toBe(404)
    // `me` says so, which is what pinned the selector.
    const me = (await (await bPage.request.get('/api/auth/me')).json()) as { legal_entities: string[] | null }
    expect(me.legal_entities).toEqual([ltdId])
  } finally {
    await context.close()
    // Back to one active legal entity for the other specs; B keeps a scope that names an
    // inactive legal entity, which the Team panel reads as «nessuna azienda attiva».
    const deactivated = await page.request.delete(`/api/legal-entities/${ltdId}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
    await page.goto('/app/settings/users')
    await expect(page.getByRole('row', { name: new RegExp(`Bea ${STAMP}`) })).toContainText(
      'nessuna azienda attiva',
    )
  }
})
