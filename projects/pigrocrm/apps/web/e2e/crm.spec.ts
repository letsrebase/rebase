import { expect, test } from '@playwright/test'
import { dragDealToStage, loginAsAdmin, typeLikeAHuman } from './helpers'

test.beforeEach(async ({ page }) => {
  await loginAsAdmin(page)
})

/**
 * Spec success criterion 1, end to end: define a field, then use it, with no
 * deploy -- and the one test in this whole suite proving the actual defect that
 * shipped in this slice's own settings screen cannot come back. `FieldsPanel.tsx`
 * derives "Chiave" from "Etichetta" as the user types (`previewSlug`, re-run on
 * every keystroke's *entire* current value) until "Chiave" is edited directly.
 * The bug this once was -- `if (!key)` instead of `if (!keyEdited)` -- derived the
 * key from whichever value the label held on the *first* keystroke and then
 * stopped, so "Settore" typed on a real keyboard produced the key "s". `.fill()`
 * cannot exercise this at all: it fires one native input event carrying the whole
 * string, which slugs correctly by accident whether the guard is right or wrong
 * (see FieldsPanel.test.tsx's own identical warning on `userEvent.type`). Only
 * `pressSequentially` (this file's `typeLikeAHuman`), a keystroke at a time,
 * tells the two apart.
 */
test('typing a field label one keystroke at a time still derives the whole key, and the field appears on the customer form immediately', async ({
  page,
}) => {
  await page.goto('/app/settings/fields')
  await page.getByRole('button', { name: /nuovo campo/i }).click()

  const dialog = page.getByRole('dialog')
  await typeLikeAHuman(dialog.getByLabel('Etichetta'), 'Settore')
  await expect(dialog.getByLabel('Chiave')).toHaveValue('settore')

  // The brief's own sample button text here was "Salva" -- FieldsPanel's create
  // dialog actually says "Crea" (`{create.isPending ? 'Creazione…' : 'Crea'}`);
  // verified against the component, not assumed from the brief.
  await dialog.getByRole('button', { name: 'Crea' }).click()
  // `exact: true` is load-bearing, not stylistic: Playwright's default text
  // match is a case-insensitive substring, and the Chiave column's own
  // `<code>settore</code>` cell contains "Settore" that way too -- confirmed
  // live (strict-mode violation, two matches) before adding this.
  await expect(page.getByText('Settore', { exact: true })).toBeVisible()

  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  await expect(page.getByLabel('Settore')).toBeVisible()
})

test('creating a customer, a person and a deal, then moving it across the board', async ({ page }) => {
  const name = `ACME ${Date.now()}`

  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  await page.getByLabel('Ragione sociale').fill(name)
  await page.getByLabel('P.IVA').fill('12345678901')
  await page.getByRole('button', { name: 'Salva' }).click()
  await expect(page.getByText(name)).toBeVisible()

  await page.goto('/app/people')
  await page.getByRole('button', { name: /nuova persona/i }).click()
  // Neither a bare `getByLabel('Nome')` nor `{ exact: true }` is right here.
  // "Nome" is required (`DynamicFieldRenderer`'s `RequiredMark` appends " *" to
  // the label), so its real accessible name is "Nome *" -- `exact: true`
  // against plain "Nome" then matches *nothing* (confirmed live: a 30s
  // timeout, not a strict-mode error). But the default, non-exact match is
  // wrong the other way: "Cognome" contains "nome" as a case-insensitive
  // substring, so plain `getByLabel('Nome')` resolves to *both* fields
  // (confirmed live too, and the brief's own sample code carries this exact
  // bug). An anchored regex is the one spelling that is neither too loose nor
  // too strict: matches "Nome"/"Nome *", never "Cognome".
  await page.getByLabel(/^Nome\b/).fill('Mario')
  await page.getByLabel('Cognome').fill('Rossi')
  await page.getByRole('button', { name: 'Salva' }).click()
  // The row, not the page. What this step has to prove is that the person is in
  // the people list, and `getByText('Rossi')` proves only that the string is
  // somewhere on it -- satisfied just as well by the «Azienda» column, which
  // renders `customer_ragione_sociale` (`features/people/columns.tsx`) and would
  // read "Rossi Ingegneria ... Srl" for anyone linked to the customer
  // `e2e/search.spec.ts` creates. The surname alone is also not unique to a
  // person: `DataTable` draws real `<tr>` rows, so the row's own accessible name
  // is the concatenation of its cells, and matching the full name inside it says
  // "this person has a row" and nothing weaker.
  await expect(page.getByRole('row', { name: /Mario Rossi/ })).toBeVisible()

  await page.goto('/app/deal')
  await page.getByRole('button', { name: /nuovo deal/i }).click()
  await page.getByLabel(/^Cliente/).click()
  await page.getByRole('option', { name }).click()
  await page.getByLabel(/^Nome\b/).fill('Progetto E2E')
  await page.getByRole('button', { name: 'Salva' }).click()

  const card = page.getByText('Progetto E2E', { exact: true })
  await expect(card).toBeVisible()

  await dragDealToStage(page, 'Progetto E2E', 'Offerta')

  // Not just "the card is still somewhere on the board" (true whether or not the
  // move actually took, since the card never disappears) -- specifically inside
  // Offerta's own column, and still there after a reload discards any optimistic
  // client state and re-reads the server's own record.
  const offerta = page
    .locator('div.flex.w-72.shrink-0.flex-col')
    .filter({ has: page.getByRole('heading', { name: 'Offerta', exact: true }) })
  await expect(offerta.getByText('Progetto E2E', { exact: true })).toBeVisible()

  await page.reload()
  const offertaAfterReload = page
    .locator('div.flex.w-72.shrink-0.flex-col')
    .filter({ has: page.getByRole('heading', { name: 'Offerta', exact: true }) })
  await expect(offertaAfterReload.getByText('Progetto E2E', { exact: true })).toBeVisible()
})

test('an invalid VAT number surfaces the API message on the field', async ({ page }) => {
  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  await page.getByLabel('Ragione sociale').fill('Test IVA')
  await page.getByLabel('P.IVA').fill('123')
  await page.getByRole('button', { name: 'Salva' }).click()

  // The message comes from the API's problem document, not from a client-side rule.
  await expect(page.getByText(/11 cifre/)).toBeVisible()
})

test('the timeline records what happened', async ({ page }) => {
  const name = `Timeline ${Date.now()}`
  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  await page.getByLabel('Ragione sociale').fill(name)
  await page.getByRole('button', { name: 'Salva' }).click()

  await page.getByText(name).click()
  await page.getByRole('tab', { name: 'Timeline' }).click()
  await expect(page.getByText('Creato')).toBeVisible()
})
