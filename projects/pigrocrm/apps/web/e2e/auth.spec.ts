import { expect, test } from '@playwright/test'
import { logout } from './helpers'

// Seeded by apps/web/scripts/e2e-setup.sh -- an admin, deliberately, so later specs
// in this same run (e2e/crm.spec.ts, e2e/custom-fields.spec.ts, e2e/kanban.spec.ts)
// can reach Settings through it too.
const EMAIL = 'e2e@pigro.it'
const PASSWORD = 'supersegreta1'

test('an unauthenticated visitor is sent to the login page', async ({ page }) => {
  await page.goto('/app/customers')
  await expect(page).toHaveURL(/\/app\/login$/)
})

test('a wrong password is rejected without saying which field was wrong', async ({ page }) => {
  await page.goto('/app/login')
  await page.getByRole('button', { name: /Accedi con la password/ }).click()
  await page.getByLabel('Email').fill(EMAIL)
  await page.getByLabel('Password').fill('sbagliata')
  await page.getByRole('button', { name: 'Accedi' }).click()

  await expect(page.getByText(/credenziali non valide/i)).toBeVisible()
  await expect(page).toHaveURL(/\/app\/login$/)
})

test('a correct login reaches the dashboard and logout returns to login', async ({ page }) => {
  await page.goto('/app/login')
  await page.getByRole('button', { name: /Accedi con la password/ }).click()
  await page.getByLabel('Email').fill(EMAIL)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Accedi' }).click()

  // `(\?|$)` rather than `(\/|$)`: `/app/` carries a `validateSearch` since slice 6, so a
  // correct login lands on `/app?tab=commerciale&da=…&a=…`. The old alternation was also
  // satisfied by `/app/login` itself, which is the failure mode `helpers.ts::login`
  // documents at length.
  await expect(page).toHaveURL(/\/app\/?(\?|$)/)
  // Two facts, because either alone is satisfied by the wrong screen: the shell knows who
  // is logged in, and the Home behind it actually rendered. This used to read
  // `Ciao E2E`, the greeting on the placeholder that stood at `/app/` until the real
  // dashboard replaced it — a string no screen prints any more. Since REB-222 the Home of
  // an empty space is the start page and the dashboard only once it holds work, and this
  // spec runs first on a freshly seeded database or after the others on a full one, so
  // either Home is a login that worked.
  await expect(page.getByText('E2E', { exact: true })).toBeVisible()
  await expect(
    page
      .getByRole('tab', { name: 'Commerciale' })
      .or(page.getByRole('heading', { name: 'Porta dentro il tuo lavoro' })),
  ).toBeVisible()

  // Through the profile menu, which is where «Esci» has lived since the UI revision of
  // 2026-09-08 -- see `helpers.ts::logout`. This spec spent thirty seconds waiting for a
  // button by that name and then failed, on every run, for a month.
  await logout(page)
})
