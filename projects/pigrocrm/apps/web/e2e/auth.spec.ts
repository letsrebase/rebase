import { expect, test } from '@playwright/test'
import { loginAsAdmin, logout } from './helpers'

// Seeded by apps/web/scripts/e2e-setup.sh -- an admin, deliberately, so later specs
// in this same run (e2e/crm.spec.ts, e2e/custom-fields.spec.ts, e2e/kanban.spec.ts)
// can reach Settings through it too.
const EMAIL = 'e2e@pigro.it'
const PASSWORD = 'supersegreta1'

test('an unauthenticated visitor is sent to the login page', async ({ page }) => {
  await page.goto('/app/customers')
  // The guard in routes/app.tsx sends the visitor to the login with the page they asked
  // for as `redirect`, which the login reads back once a session exists: that is the
  // whole URL, and a bare `/app/login$` has not matched it since that parameter exists.
  await expect(page).toHaveURL(/\/app\/login\?redirect=%2Fapp%2Fcustomers$/)
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

test('a login survives the session read its page load started and that answers after it (REB-662)', async ({ page }) => {
  // The login page asks `/api/auth/me` as it mounts and gets a 401, on which the client
  // tries `POST /api/auth/refresh` once (`lib/api.ts`), which also answers 401 on a
  // visitor with no session. On a loaded box that second answer can land after the
  // login has already answered 200, and `lib/auth.tsx` used to let the `null` it
  // resolves to overwrite the session it had just published: the person reached the
  // Home and was thrown back to the login form a moment later, with both cookies set
  // and nothing asking `me` again for thirty seconds. Here that refresh's 401 is
  // fetched when the page sends it,
  // with no session, and delivered only once the login has landed, which is the worst
  // case of that race made deterministic; the Home has to stay once it arrives. A slow
  // `me` alone is not the case: its late 401 is replayed through a refresh that by then
  // finds the new cookie, and comes back as the person.
  let loginLanded!: () => void
  const landed = new Promise<void>((resolve) => {
    loginLanded = resolve
  })
  let refreshFetched!: () => void
  const fetched = new Promise<void>((resolve) => {
    refreshFetched = resolve
  })
  // `identity/spaces` answers 401 to a visitor too (`routes/app/login.tsx`), and on
  // its own it could be the request that provokes the refresh held below, with `me`'s
  // 401 landing after the release on a slow box and healing through a refresh of its
  // own. A 200 with no space is the same screen, and it leaves `me` as the mount's one
  // 401: the held refresh is the one its retry waits on, by construction.
  await page.route('**/api/identity/spaces', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  )
  let held = 0
  await page.route('**/api/auth/refresh', async (route) => {
    // Only the first refresh of this page, the one the mount's `me` provokes.
    if (held === 0) {
      held++
      const response = await route.fetch()
      refreshFetched()
      expect(response.status()).toBe(401)
      await landed
      await route.fulfill({ response })
      return
    }
    await route.continue()
  })

  // The form is driven here rather than through `loginAsAdmin`, because a credential
  // must not be typed before the mount's refresh has been fetched: on a slow box that
  // request could otherwise go out after the login had set its cookie, come back 200,
  // and the held answer would prove nothing (CodeRabbit's adversarial pass on #561).
  await page.goto('/app/login')
  await fetched
  await page.getByRole('button', { name: /Accedi con la password/ }).click()
  await page.getByLabel('Email').fill(EMAIL)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Accedi' }).click()
  await expect(page).toHaveURL(/\/app\/?(\?|$)/)
  loginLanded()
  // The held 401 reaches the app now, after the login has been published. What is
  // asserted next is that nothing happens, which no locator can wait for: the body is
  // awaited, then a bounded settle covers the app's handling of it (a parse, a cache
  // write, a render, a router push when there was one), and only then is the screen
  // read. Before the fix the push came within that second on this box; a slower box
  // could only make this test pass without the fix, never fail with it.
  const late = await page.waitForResponse((response) => response.url().endsWith('/api/auth/refresh'))
  await late.finished()
  await page.waitForTimeout(1_000)

  await expect(page.getByText('E2E', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/app\/?(\?|$)/)
  // The late answer took nothing with it: the session the login set is still the one
  // the server knows, not only the one the cache shows.
  const me = await page.request.get('/api/auth/me')
  expect(me.status()).toBe(200)
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
