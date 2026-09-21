import { expect, test, type Page, type Response } from '@playwright/test'
import { loginAsAdmin, rowAction, typeLikeAHuman } from './helpers'

test.beforeEach(async ({ page }) => {
  await loginAsAdmin(page)
})

async function createTextField(page: Page, label: string): Promise<void> {
  await page.goto('/app/settings/fields')
  await page.getByRole('button', { name: /nuovo campo/i }).click()
  const dialog = page.getByRole('dialog')
  await typeLikeAHuman(dialog.getByLabel('Etichetta'), label)
  await dialog.getByRole('button', { name: 'Crea' }).click()
  await expect(page.getByText(label, { exact: true })).toBeVisible()
}

/** The value cell immediately to the right of a `Row`'s own label
 *  (routes/app/customers/$customerId.tsx: `<span>{label}</span><span>{value}</span>`,
 *  siblings inside one flex row) -- located relative to the label itself so it
 *  cannot be confused with an unrelated "0"/"Sì"/"No" elsewhere on a page that
 *  also shows a VAT number, a probability, or another field's own value. */
function detailValueFor(page: Page, label: string) {
  return page.getByText(label, { exact: true }).locator('xpath=following-sibling::span[1]')
}

/**
 * The first named gap beyond the brief: a field defined in Settings has to work
 * as a *record's* whole lifecycle, not just appear once. Covers all three
 * surfaces from the same field with no reload/rebuild in between (the column,
 * the create-form input, the detail row), then the one direction the brief never
 * exercises at all -- clearing a value back out and confirming the empty state
 * actually reached the database, not just this render.
 *
 * Fix round 1: clears *both* halves of the contract side by side, not only the
 * custom one -- `CustomerForm.submit`'s own docstring is explicit that the two
 * spellings are not interchangeable (a native column clears on an explicit
 * `""`, since `CustomerUpdate.model_dump(exclude_none=True)` keeps an empty
 * string but silently drops an actual `None`; a custom field clears only on an
 * explicit `null` in `custom_fields`, since `""` there is `is_blank` and a
 * non-required field just quietly skips it). Getting either spelling wrong, or
 * swapping them, is exactly the class of defect that cost this project two fix
 * rounds -- proving only the custom half, as the first draft of this test did,
 * leaves the native half's own spelling completely unguarded.
 */
test("a custom field's whole life: appears everywhere with no restart, and a cleared value -- native or custom -- stays cleared", async ({
  page,
}) => {
  await createTextField(page, 'Referente')

  // The column, with no rebuild: buildCustomerColumns (features/customers/columns.tsx)
  // reads it straight off GET /api/schema/customer, and `DataTable`'s own loading gate
  // (components/DataTable.tsx) is `customers.isLoading` -- so the columnheader depends on
  // both requests, not just one. `page.goto` here is a genuine hard reload: the whole SPA
  // bundle, the auth check, and both of those requests, all from scratch. Asserting
  // `toBeVisible()` right after `goto` bets the suite's fixed `expect.timeout` always
  // outruns a cold reload plus two round trips -- it does not (ORB-39: failed once,
  // passed on retry, in the same suite run; reproduced live under load with zero
  // columnheaders, not just this one, still on screen when the timeout hit). Waiting for
  // both responses to actually land is the fix: a test bug, not the application's --
  // `FieldDefinitionService.create` commits before the POST that creates the field even
  // returns, and `describe_entity` reads live, so the reload cannot miss the field.
  const isGet = (response: Response, path: string): boolean =>
    new URL(response.url()).pathname.endsWith(path) && response.request().method() === 'GET'
  await Promise.all([
    page.goto('/app/customers'),
    page.waitForResponse((response) => isGet(response, '/api/schema/customer')),
    page.waitForResponse((response) => isGet(response, '/api/customers')),
  ])
  await expect(page.getByRole('columnheader', { name: 'Referente' })).toBeVisible()

  // The create-form input, native (Telefono) and custom (Referente) side by side.
  const name = `Whole Life ${Date.now()}`
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  const createDialog = page.getByRole('dialog')
  await createDialog.getByLabel('Ragione sociale').fill(name)
  await createDialog.getByLabel('Telefono').fill('0212345678')
  await createDialog.getByLabel('Referente').fill('Giulia Bianchi')
  await createDialog.getByRole('button', { name: 'Salva' }).click()
  await expect(page.getByText(name)).toBeVisible()

  // The detail row, holding both values just set.
  await page.getByText(name).click()
  await expect(detailValueFor(page, 'Telefono')).toHaveText('0212345678')
  await expect(detailValueFor(page, 'Referente')).toHaveText('Giulia Bianchi')

  // Clear both...
  await page.getByRole('button', { name: 'Modifica' }).click()
  const editDialog = page.getByRole('dialog')
  await editDialog.getByLabel('Telefono').fill('')
  await editDialog.getByLabel('Referente').fill('')
  await editDialog.getByRole('button', { name: 'Salva' }).click()
  await expect(editDialog).toBeHidden()

  // ...and confirm both clears reached the database, not only this render:
  // ship either spelling wrong (or the two swapped) and the old value is
  // still sitting in the database after this reload.
  await page.reload()
  await expect(detailValueFor(page, 'Telefono')).toHaveText('—')
  await expect(detailValueFor(page, 'Referente')).toHaveText('—')
})

/**
 * The defect that cost two fix rounds: archiving a field definition while a
 * record still holds a value for it used to make that record permanently
 * uneditable (a flat form state re-derives "is this custom?" from the *active*
 * schema at submit time, which no longer lists the archived key -- see
 * `CustomerForm.tsx`'s own docstring on `CustomerFormValues` for the full
 * reproduction). The UI has no way left to show the archived value at all once
 * archived (`CustomerDetail`'s "Campi personalizzati" card only ever lists
 * `schema.data?.custom_fields`), so the only honest way to check it survived is
 * the API itself.
 */
test('archiving a field a record still holds a value for does not lock that record, and the value survives', async ({
  page,
}) => {
  await createTextField(page, 'Codice interno')

  const name = `Archived Trap ${Date.now()}`
  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  const createDialog = page.getByRole('dialog')
  await createDialog.getByLabel('Ragione sociale').fill(name)
  await createDialog.getByLabel('Codice interno').fill('RIS-042')
  await createDialog.getByRole('button', { name: 'Salva' }).click()
  await expect(page.getByText(name)).toBeVisible()

  await page.getByText(name).click()
  const customerId = new URL(page.url()).pathname.split('/').pop()
  expect(customerId).toBeTruthy()

  // Archive the definition while this record still holds a value for it.
  await page.goto('/app/settings/fields')
  // Through the row's «⋯» menu, which is where per-row actions have lived since the UI
  // revision of 2026-09-08 -- see `helpers.ts::rowAction`.
  await rowAction(page, 'Codice interno', 'Archivia')
  // `exact: true` is load-bearing: the success toast this click fires reads
  // "Campo archiviato" (FieldsPanel's `archive.mutate` `onSuccess`), which
  // contains "archiviato" as a case-insensitive substring of the plain
  // "Archiviato" status badge -- confirmed live (strict-mode violation, two
  // matches) before adding this.
  await expect(page.getByText('Archiviato', { exact: true })).toBeVisible()

  // The UI genuinely cannot show it any more -- this is exactly why the
  // assertions below go through the API instead.
  await page.goto(`/app/customers/${customerId}`)
  await expect(page.getByText('Codice interno', { exact: true })).not.toBeVisible()

  // Edit the record, changing something unrelated. This must save cleanly --
  // no error banner, no field it cannot attach a problem to (DynamicForm's own
  // `unattributedMessage` is exactly what used to surface this 422 as a banner
  // naming a field the form no longer renders at all).
  await page.getByRole('button', { name: 'Modifica' }).click()
  const editDialog = page.getByRole('dialog')
  await editDialog.getByLabel('Comune').fill('Milano')
  await editDialog.getByRole('button', { name: 'Salva' }).click()
  await expect(editDialog).toBeHidden()
  await expect(page.getByRole('alert')).not.toBeVisible()

  // Both facts matter, not just one: if the save had silently failed instead of
  // succeeding, the archived value would trivially "survive" (nothing happened
  // at all) while Comune would just as trivially still be unset -- checking only
  // the archived field would not tell a real fix apart from the original bug.
  const response = await page.request.get(`/api/customers/${customerId}`)
  expect(response.ok()).toBe(true)
  const customer = await response.json()
  expect(customer.comune).toBe('Milano')
  expect(customer.custom_fields.codice_interno).toBe('RIS-042')
})

/**
 * `0` and `false` are real, present values, not stand-ins for "nothing here" --
 * `renderFieldValue`'s own docstring calls out `!0 === true` in JavaScript by
 * name as the trap this guards. A checkbox goes further still: it is the one
 * type with no third state at all, so an *absent* checkbox on a record that
 * predates the field must read exactly the same as a stored `false` ("No"),
 * never a dash -- covered here with a customer created before either field
 * exists, not merely one where the field was left unchecked on the same form.
 */
test('a numeric zero and a checkbox both read as real values, never as a dash', async ({ page }) => {
  const preexistingName = `Pre-esistente ${Date.now()}`
  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  await page.getByLabel('Ragione sociale').fill(preexistingName)
  await page.getByRole('button', { name: 'Salva' }).click()
  await expect(page.getByText(preexistingName)).toBeVisible()

  // Both fields are defined *after* the customer above already exists.
  await page.goto('/app/settings/fields')
  await page.getByRole('button', { name: /nuovo campo/i }).click()
  let dialog = page.getByRole('dialog')
  await typeLikeAHuman(dialog.getByLabel('Etichetta'), 'Sconto applicato')
  await dialog.getByRole('combobox', { name: /tipo/i }).click()
  await page.getByRole('option', { name: 'Numero' }).click()
  await dialog.getByRole('button', { name: 'Crea' }).click()
  await expect(page.getByText('Sconto applicato', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: /nuovo campo/i }).click()
  dialog = page.getByRole('dialog')
  await typeLikeAHuman(dialog.getByLabel('Etichetta'), 'Attivo campagna')
  await dialog.getByRole('combobox', { name: /tipo/i }).click()
  await page.getByRole('option', { name: 'Sì / No' }).click()
  await dialog.getByRole('button', { name: 'Crea' }).click()
  await expect(page.getByText('Attivo campagna', { exact: true })).toBeVisible()

  // The pre-existing customer never had an opinion on either field -- the
  // checkbox must still read "No", never a dash, and never "Sì" either.
  await page.goto('/app/customers')
  await page.getByText(preexistingName).click()
  await expect(detailValueFor(page, 'Attivo campagna')).toHaveText('No')

  // A second, brand-new customer sets the numeric field to exactly 0 and
  // switches the checkbox on.
  const zeroName = `Valori Zero ${Date.now()}`
  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  const createDialog = page.getByRole('dialog')
  await createDialog.getByLabel('Ragione sociale').fill(zeroName)
  await createDialog.getByLabel('Sconto applicato').fill('0')
  await createDialog.getByLabel('Attivo campagna').click()
  await createDialog.getByRole('button', { name: 'Salva' }).click()
  await expect(page.getByText(zeroName)).toBeVisible()

  await page.getByText(zeroName).click()
  await expect(detailValueFor(page, 'Sconto applicato')).toHaveText('0')
  await expect(detailValueFor(page, 'Attivo campagna')).toHaveText('Sì')

  // And the same zero, scoped to this customer's own row, on the list column --
  // `!0` is `true` in JavaScript, which is exactly the trap that would render
  // this cell as blank/dash instead.
  await page.goto('/app/customers')
  const zeroRow = page.getByRole('row').filter({ hasText: zeroName })
  await expect(zeroRow.getByRole('cell', { name: '0', exact: true })).toBeVisible()
})
