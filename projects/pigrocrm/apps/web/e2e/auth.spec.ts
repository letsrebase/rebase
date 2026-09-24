import { expect, test } from '@playwright/test'
import { loginAsAdmin, logout } from './helpers'

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

test('the shell scrolls inside main only, not the whole document (REB-418)', async ({ page }) => {
  // The get-started page is long enough (four steps, each with a screen and a prompt)
  // to expose a leak that a shorter page hides: before the fix, `main` had no CSS
  // containing block, so the `sr-only` "Fatto: "/"Da fare: " prefix the page puts on
  // every step -- a plain `position: absolute` with no explicit `top` -- fell back to
  // its static position against the page itself, escaping `main`'s `overflow-y-auto`
  // and inflating `<html>`'s real height. The visible symptom was the whole document
  // scrolling, the sidebar dragged along with it, instead of only `main`.
  await loginAsAdmin(page)
  await page.goto('/app/get-started')
  await expect(page.getByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeVisible()

  const before = await page.evaluate(() => {
    const main = document.querySelector('main')!
    return {
      docOverflow: document.documentElement.scrollHeight - window.innerHeight,
      // The page has to genuinely be taller than the viewport for this test to mean
      // anything: a `main` with nothing to scroll would pass the assertions below for
      // the wrong reason, the exact gap Greptile's review caught (REB-418).
      mainOverflow: main.scrollHeight - main.clientHeight,
    }
  })
  expect(before.docOverflow).toBe(0)
  expect(before.mainOverflow).toBeGreaterThan(0)

  // `main` is the one scroller: scrolling it all the way down has to actually move its
  // content, reaching the last of the four steps, while the document itself and the
  // sidebar stay exactly where they are.
  const asideTopBefore = await page.evaluate(
    () => document.querySelector('aside')!.getBoundingClientRect().top,
  )
  const after = await page.evaluate(() => {
    const main = document.querySelector('main')!
    main.scrollTop = main.scrollHeight
    return { mainScrollTop: main.scrollTop, windowScrollY: window.scrollY }
  })
  expect(after.mainScrollTop).toBeGreaterThan(0)
  expect(after.windowScrollY).toBe(0)
  await expect(page.getByRole('heading', { name: 'Oppure a mano' })).toBeVisible()
  const asideTopAfter = await page.evaluate(
    () => document.querySelector('aside')!.getBoundingClientRect().top,
  )
  expect(asideTopAfter).toBe(asideTopBefore)
})
