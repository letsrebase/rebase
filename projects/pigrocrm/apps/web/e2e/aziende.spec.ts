import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { login, loginAsAdmin } from './helpers'

/**
 * A member scoped to one azienda sees that azienda and nothing else (REB-635, spec
 * 2026-10-03 §1.11, §4, §5). The stack runs the API as the application role
 * (`scripts/e2e-env.sh`), so what B does not see here is what Postgres refuses to show,
 * not what a `WHERE` left out.
 *
 * A, the space's admin, opens a second azienda and one customer under each; B is created
 * and then scoped from the Team panel, through the UI this card adds. B signs in on a
 * second browser context, so A's session stays alive for the clean-up at the end: the
 * second azienda is deactivated again, since another spec's «Analisi › Fiscale» expects
 * one active azienda and the files of this suite share the stack.
 */

const STAMP = Date.now()
const LTD = `Scope Ltd ${STAMP}`
const B_EMAIL = `scoped-${STAMP}@pigro.it`
const B_PASSWORD = 'collabora123'

async function createAzienda(request: APIRequestContext): Promise<string> {
  const created = await request.post('/api/aziende', {
    data: {
      nome: LTD,
      ragione_sociale: LTD,
      nazione: 'GB',
      // A foreign company: no Italian regime, a VAT rate and no natura, the body
      // «Nuova azienda» sends for «Estero» (REB-632).
      fiscal_profile: {
        pack_id: 'non-it',
        codice_regime: null,
        aliquota_iva_default: '20.00',
        natura_default: null,
        riferimento_normativo: null,
        applica_bollo: false,
        coefficiente_redditivita: null,
        aliquota_imposta_sostitutiva: null,
        aliquota_inps: null,
      },
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  return ((await created.json()) as { id: string }).id
}

async function createCustomer(
  request: APIRequestContext,
  nome: string,
  aziendaId: string,
): Promise<string> {
  const created = await request.post('/api/customers', {
    data: { ragione_sociale: nome, azienda_id: aziendaId },
  })
  expect(created.status(), await created.text()).toBe(201)
  return ((await created.json()) as { id: string }).id
}

async function scopeFromTheTeamPanel(page: Page, nome: string, keep: string): Promise<void> {
  await page.goto('/app/settings/users')
  const row = page.getByRole('row', { name: new RegExp(nome) })
  await expect(row).toContainText('Tutte')
  await row.getByRole('button', { name: `Azioni per ${nome}` }).click()
  await page.getByRole('menuitem', { name: 'Aziende…' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText(`Aziende di ${nome}`)
  // Every azienda but `keep` unchecked: a stack kept alive between runs may hold more
  // active aziende than the two this spec makes, and B must end up with exactly one.
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
  const aziende = (await (await page.request.get('/api/aziende')).json()) as {
    id: string
    nome: string
    predefinita: boolean
  }[]
  const studio = aziende.find((a) => a.predefinita)
  expect(studio).toBeDefined()
  const ltdId = await createAzienda(page.request)
  const studioCustomer = await createCustomer(page.request, `Studio Cliente ${STAMP}`, studio!.id)
  const ltdCustomer = await createCustomer(page.request, `Ltd Cliente ${STAMP}`, ltdId)

  // B, created by A with a password (no mail leaves the e2e stack), then scoped to
  // «Scope Ltd» from the Team panel: the row said «Tutte», it says the azienda now.
  const created = await page.request.post('/api/users', {
    data: { email: B_EMAIL, password: B_PASSWORD, nome: `Bea ${STAMP}`, ruolo: 'collaboratore' },
  })
  expect(created.status(), await created.text()).toBe(201)
  await scopeFromTheTeamPanel(page, `Bea ${STAMP}`, LTD)

  const context = await browser.newContext()
  const bPage = await context.newPage()
  try {
    await login(bPage, B_EMAIL, B_PASSWORD)
    // The sidebar: the azienda's name where the selector would be, and no selector.
    await expect(bPage.getByLabel('Azienda')).toContainText(LTD)
    await expect(bPage.getByRole('combobox', { name: 'Azienda' })).toHaveCount(0)
    // The customers list: B's azienda's row and not A's.
    await bPage.goto('/app/customers')
    await expect(bPage.getByText(`Ltd Cliente ${STAMP}`)).toBeVisible()
    await expect(bPage.getByText(`Studio Cliente ${STAMP}`)).toHaveCount(0)
    // By id: the other azienda's row does not exist for B, and the one B may see does.
    expect((await bPage.request.get(`/api/customers/${studioCustomer}`)).status()).toBe(404)
    expect((await bPage.request.get(`/api/customers/${ltdCustomer}`)).status()).toBe(200)
    expect((await bPage.request.get(`/api/aziende/${studio!.id}`)).status()).toBe(404)
    // `me` says so, which is what pinned the selector.
    const me = (await (await bPage.request.get('/api/auth/me')).json()) as { aziende: string[] | null }
    expect(me.aziende).toEqual([ltdId])
  } finally {
    await context.close()
    // Back to one active azienda for the other specs; B keeps a scope that names an
    // inactive azienda, which the Team panel reads as «nessuna azienda attiva».
    const deactivated = await page.request.delete(`/api/aziende/${ltdId}`)
    expect(deactivated.status(), await deactivated.text()).toBe(200)
    await page.goto('/app/settings/users')
    await expect(page.getByRole('row', { name: new RegExp(`Bea ${STAMP}`) })).toContainText(
      'nessuna azienda attiva',
    )
  }
})
