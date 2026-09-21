import { existsSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { killApi, loginAsAdmin, navigate, relaunchApi } from './helpers'

const PIDFILE = process.env.PIGROCRM_E2E_API_PIDFILE ?? '/tmp/pigrocrm-e2e-api.pid'

test.beforeEach(() => {
  expect(existsSync(PIDFILE), `${PIDFILE} missing -- run this suite via apps/web/scripts/e2e.sh`).toBe(true)
})

/**
 * The named gap on the list screens: a failed request must read as "we could
 * not ask", never as a quiet, confident "there is nothing here" --
 * `QueryErrorBanner.tsx`'s own docstring calls out the exact live defect this
 * closes ("Nessun cliente" on a list whose request never actually completed).
 * Self-contained on purpose: this test kills the real API process mid-suite
 * and relaunches it itself before finishing, so every spec file after this one
 * -- regardless of run order -- still has a working backend, rather than
 * relying on this file happening to run last.
 */
test('a dead API reads as a failure, not an empty list, and the app recovers once it returns', async ({ page }) => {
  test.setTimeout(60_000)

  await loginAsAdmin(page)
  await page.goto('/app/customers')
  // Confirms the list genuinely works before the API dies, so what follows is a
  // regression against a real success, not against a page that never loaded.
  await expect(page.getByRole('button', { name: /nuovo cliente/i })).toBeVisible()

  await killApi()

  try {
    // A brand-new query, not a reload: reloading the document would also re-run
    // `GET /api/auth/me`, and `AuthProvider`'s own try/catch (lib/auth.tsx)
    // treats *any* failure there -- including the network error this test just
    // caused -- as "not authenticated", bouncing to /login and masking the
    // thing actually under test. Typing into the search box changes
    // `useCustomers`' own query key instead, which has never been fetched
    // before and so has no stale-but-cached data to fall back on.
    await page.getByPlaceholder(/cerca per ragione sociale/i).fill('la richiesta deve fallire')

    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page.getByText('Nessun cliente. Creane uno per iniziare.')).not.toBeVisible()
  } finally {
    await relaunchApi()
  }

  // A real recovery, not merely "a banner appeared once" -- and specifically
  // *not* clearing the search box back to '' and re-checking, which was this
  // test's first draft: `queryKeys.customers({})` was already fetched
  // successfully before the API died and is still within react-query's own
  // 30s staleTime (lib/query.ts), so that query never re-hits the network at
  // all and the check would pass even against a *still-broken* relaunch --
  // caught by inspecting the relaunched process's own environment while
  // building this suite (see task-10-report.md). A full reload forces every
  // query to refetch from nothing, `GET /api/auth/me` included -- which is
  // also the one request that can only succeed if the relaunched process was
  // handed the *same* PIGROCRM_JWT_SECRET, since that is what verifies the
  // still-current session cookie's signature. Staying on /app/customers here is
  // therefore proof of both a working database connection and a correctly
  // propagated secret, not just an HTTP server that answers.
  await page.reload()
  await expect(page).toHaveURL(/\/app\/customers/)
  await expect(page.getByRole('alert')).not.toBeVisible()
  await expect(page.getByRole('button', { name: /nuovo cliente/i })).toBeVisible()
})

/**
 * Fix round 1's Critical finding: `routes/app/deal/index.tsx` has its own,
 * *separate* implementation of the identical guard the test above exercises
 * for Clienti -- `KanbanBoard` has no `DataTable` underneath it to inherit
 * that coverage from, so `DealsKanban` hand-rolls `boardUnavailable` itself
 * (`stagesUnavailable || dealsUnavailable`, each `isError && data.length ===
 * 0`). No spec ever visited the Kanban board with the API down, and a
 * reviewer setting `boardUnavailable` to a hardcoded `false` -- disabling the
 * guard outright -- still passed all 13 tests. That is the exact failure mode
 * this whole task exists to prevent.
 *
 * The Kanban page has no search box or other filter to force a brand-new,
 * never-cached query the way the Clienti test above does, so this test uses a
 * different lever to the same end: kill the API *before* this browser session
 * has ever visited `/app/deal` at all, then navigate there with an in-app
 * (client-side) link click, not `page.goto`. A client-side transition does not
 * re-run `GET /api/auth/me` -- that query is already cached and fresh from the
 * login above -- so the auth guard reads the cached session and never touches
 * the network; `useStages`/`useDeals`, mounting for the very first time in
 * this session, always fetch on that first mount regardless of staleTime, and
 * hit the dead API for real.
 */
test('a dead API reads as a failure on the Kanban board too, not an empty pipeline', async ({ page }) => {
  test.setTimeout(60_000)

  await loginAsAdmin(page)
  await killApi()

  try {
    // Through the group, closed on the dashboard since the sidebar became grouped --
    // see `helpers.ts::navigate`. Arriving *by clicking* rather than by `goto` is
    // deliberate: the point is that `useDeals` mounts for the first time in this
    // session and really does hit the dead API.
    await navigate(page, 'Vendite', 'Deal')
    await expect(page).toHaveURL(/\/app\/deal/)

    await expect(page.getByRole('alert')).toBeVisible()
    // The exact shape the live defect took before `boardUnavailable` existed:
    // every column rendered anyway, each reading 0 deals / 0,00 € with
    // "Nessun deal" -- indistinguishable from a tenant with a genuinely empty
    // pipeline. Neither the empty-column copy nor a single column heading may
    // appear while the request has failed. (The "Nuovo deal" button is *not*
    // part of this check on purpose -- it lives in the page header, outside
    // `boardUnavailable`'s own conditional, so it stays visible either way;
    // asserting it away here would fail against the correct implementation.)
    await expect(page.getByText('Nessun deal')).not.toBeVisible()
    await expect(page.getByRole('heading', { name: 'Lead', exact: true })).not.toBeVisible()
  } finally {
    await relaunchApi()
  }

  // Recovery: a fresh navigation (this route was never successfully loaded
  // before in this session, so there is no stale cache to trip over the way
  // the Clienti test above has to guard against) shows the real board again.
  await page.reload()
  await expect(page.getByRole('alert')).not.toBeVisible()
  await expect(page.getByRole('heading', { name: 'Lead', exact: true })).toBeVisible()
})
