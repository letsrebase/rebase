/**
 * **Criterion 5.** No silent partial result, and no empty list drawn after a failure.
 *
 * Three of these assertions cannot be made honestly anywhere else: one needs a real
 * network to observe that no request was issued at all, one needs a backend that refuses
 * the query while the rest of the app keeps working, and one needs 500 real rows so that
 * truncation happens rather than being simulated by a fixture that says it did.
 *
 * **What the API does when one branch fails, checked before this file was written.**
 * `SearchService.search_everything` builds its four groups eagerly, in one expression,
 * with no `try` anywhere in the service or in `SearchRepository`: a failure in any one
 * branch propagates out of the whole call and the endpoint answers with a problem
 * document. There is no code path that returns three groups and calls it an answer, so
 * "a partial result arrives labelled as a whole one" is not a state this suite can
 * reach — and the failing-search test below is therefore the honest shape of criterion 5
 * in a browser: the whole search fails, and the UI must say so instead of drawing an
 * empty list. The one real gap is a different one and is not silent here: `invoice` is a
 * declared `SearchEntity` that no repository branch searches yet (6C's Task C13), so the
 * response carries four groups, not five.
 */
import { expect, test, type Page } from '@playwright/test'
import { loginAsAdmin, seedCustomers } from './helpers'

/**
 * Opens the palette with the shortcut and hands back its input.
 *
 * The wait on the header's own button is load-bearing: `page.goto` resolves on the
 * document's load event, React mounts after that, and a keypress in the gap reaches no
 * listener at all -- a race that reads exactly like a broken shortcut.
 */
async function openPalette(page: Page) {
  await expect(page.getByRole('button', { name: /cerca/i })).toBeVisible()
  await page.keyboard.press('ControlOrMeta+k')
  const input = page.getByRole('combobox')
  await expect(input).toBeFocused()
  return input
}

test.describe('ricerca globale', () => {
  test.beforeEach(async ({ page }) => {
    // `loginAsAdmin` already lands on /app and asserts it. No second `goto` here: the
    // SPA is served under the base /app/ (vite.config.ts), so navigating to "/app" with
    // no trailing slash gets Vite's own "did you mean /app/?" page instead of the app,
    // and every assertion after it fails for a reason that has nothing to do with search.
    await loginAsAdmin(page)
  })

  test('si apre con Cmd/Ctrl+K da qualunque schermata', async ({ page }) => {
    // Not the dashboard: the palette is mounted by the shell, so it has to answer on a
    // screen that has a search box of its own competing for the shortcut.
    await page.goto('/app/customers')
    await openPalette(page)
  })

  test('sotto i tre caratteri invita a scrivere e non emette nessuna richiesta', async ({
    page,
  }) => {
    const searchRequests: string[] = []
    page.on('request', (request) => {
      if (request.url().includes('/api/search')) searchRequests.push(request.url())
    })

    const input = await openPalette(page)
    await input.fill('ro')
    // Well past the 250 ms debounce: the assertion is that nothing was ever sent, not
    // that nothing had been sent yet.
    await page.waitForTimeout(1500)

    await expect(page.getByText(/continua a scrivere/i)).toBeVisible()
    expect(searchRequests).toEqual([])
    await expect(page.getByText(/nessun risultato/i)).toHaveCount(0)
  })

  test('con 500 corrispondenze mostra 5 per classe, il conteggio reale e «vedi tutti»', async ({
    page,
  }) => {
    // 500 real rows through the real API is the slow part, and it is the point: the
    // count ceiling is a property of the SQL, not of a fixture that claims a number.
    test.setTimeout(180_000)
    await seedCustomers(page, 'Truncato', 500)
    const input = await openPalette(page)
    await input.fill('Truncato')

    // "oltre 200": the count is exact to 200 and declared as a minimum beyond it (§8.5).
    // The palette must never render the bare number 200, which would be a lie.
    await expect(page.getByText(/oltre 200/i).first()).toBeVisible()
    await expect(page.getByText('Clienti · 200', { exact: true })).toHaveCount(0)

    const options = page.getByRole('option')
    // Five hits plus the "vedi tutti" row, in the one group that matched.
    await expect(options).toHaveCount(6)
    await expect(page.getByRole('option', { name: /vedi tutti/i })).toBeVisible()
  })

  test("«vedi tutti» porta all'elenco filtrato con lo stesso termine", async ({ page }) => {
    await seedCustomers(page, 'Elenco', 12)
    const input = await openPalette(page)
    await input.fill('Elenco')
    await page.getByRole('option', { name: /vedi tutti/i }).click()

    await expect(page).toHaveURL(/\/app\/customers\?search=Elenco/)
    // The URL carrying the term is only half of the promise: the list has to be the one
    // the palette was describing. Stated as "every row but the header is an Elenco row"
    // rather than as an exact count, because the seeded rows are the only ones this
    // assertion owns -- an unfiltered list would put fifty other customers here.
    const rows = page.getByRole('row')
    await expect(rows.filter({ hasNotText: 'Elenco' })).toHaveCount(1)
    expect(await rows.filter({ hasText: 'Elenco' }).count()).toBeGreaterThanOrEqual(12)
  })

  test('un termine senza corrispondenze lo dice, e nomina il termine', async ({ page }) => {
    const input = await openPalette(page)
    await input.fill('zzzqqqwww')

    await expect(page.getByText(/nessun risultato per/i)).toBeVisible()
    await expect(page.getByText(/zzzqqqwww/)).toBeVisible()
  })

  test("con la ricerca che fallisce mostra l'errore e NON «Nessun risultato»", async ({ page }) => {
    // The database refusing the query, at the only boundary a browser test can reach: the
    // response. A 500 with a problem document is exactly what `domain_error_handler`
    // produces, so the client sees the real shape and not an invented one. Only
    // `/api/search` is intercepted -- everything else on the page keeps working, which is
    // what makes "the search is unavailable" distinguishable from "the app is down".
    await page.route('**/api/search**', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          type: 'about:blank',
          title: 'Errore interno',
          status: 500,
          detail: 'Ricerca non disponibile',
        }),
      })
    })

    const input = await openPalette(page)
    await input.fill('Rossi')

    // `lib/query.ts` retries twice with backoff on anything that is not a 401/403, so the
    // banner takes a moment. `playwright.config.ts` already raises the expect timeout to
    // 8 s for exactly this reason (see its own comment).
    await expect(page.getByRole('alert')).toContainText(/ricerca non disponibile/i)

    // The assertion §8.6 exists for. An empty list drawn after an error *is* a wrong
    // answer: it says "there is none" when the truth is "I do not know".
    await expect(page.getByText(/nessun risultato/i)).toHaveCount(0)
    await expect(page.getByRole('option')).toHaveCount(0)
    // Nor a spinner that never resolves: on a failure the query is no longer pending
    // while its data is still undefined, so a loading branch checked before the error
    // one answers a failed search with «Ricerca in corso…» forever.
    await expect(page.getByText(/ricerca in corso/i)).toHaveCount(0)
  })

  test('un frammento di partita IVA trova il cliente', async ({ page }) => {
    // Unique per run, name and number both: this database is not truncated between
    // invocations, and a fixed P.IVA would make the second run of this spec match two
    // customers and fail on a duplicate that is the fixture's fault, not the search's.
    const partitaIva = String(Date.now()).slice(-11)
    // The name deliberately carries no digits: the only place the searched fragment can
    // be found is the P.IVA, which is a column this row does not display.
    const ragioneSociale = `Rossi Ingegneria ${Date.now().toString(36).replace(/[0-9]/g, 'x')} Srl`
    const created = await page.request.post('/api/customers', {
      data: { ragione_sociale: ragioneSociale, partita_iva: partitaIva },
    })
    expect(created.status(), await created.text()).toBe(201)

    const input = await openPalette(page)
    // A fragment from the middle of the number: a prefix would also be served by a
    // plain `LIKE 'x%'`, and §8.1's promise is that an infix finds it.
    await input.fill(partitaIva.slice(3, 9))

    await expect(page.getByRole('option', { name: ragioneSociale })).toBeVisible()
  })
})
