/**
 * **Criterion 15**, the human half — and criterion 2, observed live rather than argued.
 *
 * The criterion's own shape is "Claude does this, the human does that", so it is split
 * across two surfaces: the agent half is a Python test against the in-process MCP server
 * (`apps/mcp/tests/`), this half is a real browser against a real uvicorn. Splitting it is
 * not a weakening. The two adapters cannot be driven from one file at all — `apps/mcp/
 * ruff.toml` forbids importing `pigrocrm_api` under `TID251` and `apps/api/ruff.toml`
 * forbids `pigrocrm_mcp` symmetrically — and a single harness pretending to drive both
 * would have to fake one of them.
 *
 * **What this file proves that no unit test can.** Every assertion below crosses at least
 * one boundary a component test replaces with a mock:
 *
 *  - accepting an offer in the browser runs `AutomationRunner` inside the *same* database
 *    transaction as the state change, and the deal comes back moved. `OfferStatePicker.
 *    test.tsx` asserts the button posts; only this asserts the automation ran;
 *  - the movement's timeline entry is written by `Actor.system()` with the human in
 *    `attivata_da`, so "the system did it, because you accepted that offer" is one row and
 *    not two — and reading it requires the row to have survived a real commit;
 *  - the commercial signal counts a predicate over three tables. Watching it go **down by
 *    one** because a deal was repaired is the only way to see that the card and the
 *    automation are talking about the same rows;
 *  - and the operational signal's drill-through is compared, number for number, against the
 *    list it opens. That is criterion 2 — "a card and its drill-through are the same
 *    predicate, not two calculations" — and it is exactly the assertion that would have
 *    caught the version of this screen whose links pointed at query parameters the routes
 *    ignored, where the drill-through showed the *whole* list under a label promising a
 *    filtered one.
 *
 * Everything this file seeds is prefixed `Ciclo` and stamped per run:
 * `playwright.config.ts` runs `workers: 1, fullyParallel: false` against one database that
 * is never truncated between invocations.
 */
import { expect, test, type Page } from '@playwright/test'
import { ensureSpaceHasWork, loginAsAdmin, seedCycleFixture, seedDealWithRate } from './helpers'

/**
 * Today, from the browser host's own calendar parts.
 *
 * Never a literal date and never a literal year: `InvoiceService._check_issue_date` refuses
 * anything outside the current one, and a hard-coded `2026` is a spec that starts failing on
 * the first of January. Never `toISOString().slice(0, 10)` either — that projects through
 * UTC, so anywhere behind it the date loses a day and on 31 December it loses a year, which
 * is the trap `lib/dates.ts` documents from the rendering side.
 */
function isoToday(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/** The first and last day of the current month, as the period picker writes them. */
function currentMonth(): { da: string; a: string } {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  // Day 0 of the *next* month is the last day of this one, and `Date` does the carry over
  // December for us.
  const last = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
  return {
    da: `${now.getFullYear()}-${month}-01`,
    a: `${now.getFullYear()}-${month}-${String(last).padStart(2, '0')}`,
  }
}

/**
 * The «offerta accettata, deal non vinto» count, read from the API.
 *
 * It used to be read off the commercial tab, through the paragraph that named it. That
 * paragraph is gone: the Home revision of 2026-09-09 left that tab with the first row and
 * the pipeline and nothing else -- «no offers, no signals, no detail table», which
 * `CommercialTab.test.tsx` now asserts as an absence. So this spec was asserting the
 * visibility of a sentence the product had deliberately stopped printing, and timed out on
 * every run.
 *
 * The figure itself did not go anywhere: `CommercialDashboard.offerte_accettate_deal_non_vinto`
 * is still computed, still the permanent cross-check on automation A1, and still what makes
 * the step below («the signal, after: down by one») worth asserting at all. Read where it
 * lives now. When a screen prints it again, this helper is the one place to point back at
 * the screen.
 */
async function readSignal(page: Page): Promise<number> {
  const { da, a } = currentMonth()
  const response = await page.request.get('/api/dashboard/sales', { params: { da, a } })
  expect(response.status(), await response.text()).toBe(200)
  const body = (await response.json()) as { offerte_accettate_deal_non_vinto: number }
  const value = body.offerte_accettate_deal_non_vinto
  // A `NaN` compared with `>=` is silently false, so a field that stopped being a number
  // would look like a product failure. Say so instead.
  expect(Number.isInteger(value), `il segnale non è un numero: ${String(value)}`).toBe(true)
  return value
}

test.describe('il ciclo completo — metà umana', () => {
  test("accettare un'offerta muove il deal, lo scrive in timeline e fa scendere il segnale", async ({
    page,
  }) => {
    await loginAsAdmin(page)
    const me = (await (await page.request.get('/api/auth/me')).json()) as { id: string }
    const cycle = await seedCycleFixture(page)

    // 1. The signal, before. At least one, and the fixture is what guarantees it: an
    //    assertion that would also pass at zero would pass against a signal wired to
    //    nothing.
    const before = await readSignal(page)
    expect(before).toBeGreaterThanOrEqual(1)

    // 2. The human resolves the customer from a fragment of its VAT number — the palette,
    //    which is criterion 15's human-side twin of the agent's `search_everything`. A
    //    fragment from the middle of the number, never a prefix: a prefix would also be
    //    served by a plain `LIKE 'x%'`, and §8.1 promises an infix finds it.
    await expect(page.getByRole('button', { name: /cerca/i })).toBeVisible()
    await page.keyboard.press('ControlOrMeta+k')
    const palette = page.getByRole('combobox')
    await expect(palette).toBeFocused()
    await palette.fill(cycle.partitaIva.slice(3, 9))
    await page.getByRole('option', { name: cycle.customerName }).click()
    await expect(page).toHaveURL(new RegExp(`/app/customers/${cycle.customerId}$`))

    // 3. From the customer to the deal, and from the deal's own Documenti tab to the offer.
    //    Not a `goto` straight to the document: the point of the cycle is that these screens
    //    are joined up, and a direct URL would skip the join.
    await page.goto(`/app/deal/${cycle.dealId}`)
    await expect(page.getByRole('heading', { name: cycle.dealName })).toBeVisible()
    // The deal has not been won yet, and saying so here is what makes the assertion after
    // the acceptance mean something: without it, "Vinto is on screen" could have been true
    // all along.
    //
    // Read off the stage bar and not off the page: the Pipedrive-style deal page of
    // 2026-09-09 put a «Vinto» *button* in the header (the outcome a person chooses by
    // hand), so a page-wide `getByText('Vinto')` matched that button while the deal was
    // still open -- and, worse, would have matched it again after the acceptance, making
    // step 5 below pass whether or not automation A1 had ever run. `DealStageBar` says
    // where the deal *is*: «Fase attuale: X» while it is open, the outcome's own word
    // once it is closed.
    const fase = page.getByRole('group', { name: 'Fase del deal' })
    await expect(fase).toContainText('Fase attuale:')
    await expect(fase).not.toContainText('Vinto')

    await page.getByRole('tab', { name: 'Documenti' }).click()
    await page.getByRole('link', { name: cycle.documentTitle }).click()
    await expect(page.getByRole('heading', { name: cycle.documentTitle })).toBeVisible()

    // 4. The decision, taken the way a human takes it. `OfferStatePicker` draws its buttons
    //    from the server's own transition table, so this is the only button offered.
    await page.getByRole('button', { name: 'Segna come Accettata' }).click()
    await expect(page.getByText('Accettata')).toBeVisible()
    // `accettata` is terminal (`OFFER_TRANSITIONS`), so the picker must now offer nothing.
    await expect(page.getByRole('button', { name: /^Segna come/ })).toHaveCount(0)

    // 5. Automation A1 ran inside that same transaction: the deal is won. Nothing in the
    //    browser asked for this — it is the whole claim §9.5 makes about an automation
    //    being a property of the write and not a job somebody has to notice.
    await page.goto(`/app/deal/${cycle.dealId}`)
    await expect(page.getByRole('group', { name: 'Fase del deal' })).toContainText('Vinto')

    // 6. And it said so, once, attributed to the system and naming the human who triggered
    //    it. Two facts in one row, which is the point: "the system moved it" and "because
    //    you accepted that offer" are useless apart.
    await page.getByRole('tab', { name: 'Timeline' }).click()
    // Narrowed to A1, because A2 writes an entry of the *same* kind on this same deal: the
    // first offer going `inviata` advanced it to «Offerta». Two automations, two rows, and
    // an assertion that did not say which rule it meant would pass on either of them.
    const movimento = page
      .getByRole('listitem')
      .filter({ hasText: /Automazione\.stage spostato/ })
      .filter({ hasText: 'Regola: A1' })
    await expect(movimento).toHaveCount(1)
    await expect(movimento).toContainText('Sistema')
    // Not the user's own badge: an automation attributed to whoever happened to trigger it
    // is indistinguishable from a change that person made by hand.
    await expect(movimento).not.toContainText('Utente')
    await expect(movimento).toContainText('Regola: A1')
    await expect(movimento).toContainText('A: Vinto')
    await expect(movimento).toContainText(`Attivata da: ${me.id}`)

    // 7. The signal, after: **down by one**.
    //
    //    Both offers hang off this one deal, which is what makes the movement visible at
    //    all. The offer just accepted is a new accepted offer, so it is a `+1` — and the
    //    same transaction won the deal, so it is a `-1` in the same instant and the two
    //    cancel. What actually moves is the *stale* offer seeded before it: it was counted
    //    while its deal sat open, and winning that deal is exactly the repair this signal
    //    exists to ask for. If A1 had not fired, this number would be `before + 1`.
    expect(await readSignal(page)).toBe(before - 1)
  })

  test('il segnale «Vinto ma da fatturare» e il suo elenco dicono lo stesso numero', async ({
    page,
  }) => {
    await loginAsAdmin(page)
    const stamp = Date.now().toString(36)
    const me = (await (await page.request.get('/api/auth/me')).json()) as { id: string }
    const stages = (await (await page.request.get('/api/pipeline-stages')).json()) as {
      id: string
      code: string | null
    }[]
    const vinto = stages.find((stage) => stage.code === 'vinto')
    if (!vinto) throw new Error('lo stato «vinto» non è fra gli stati seminati')

    // A deal that satisfies the predicate: won, with a billable hour nobody has put on a
    // document. Seeded so the count is at least one — a card and a list that agree on zero
    // agree about nothing, and would go on agreeing with the filter deleted.
    const { dealId } = await seedDealWithRate(page.request, {
      nome: `Ciclo da fatturare ${stamp}`,
      tariffa: '90.000000',
    })
    const entry = await page.request.post('/api/time-entries', {
      data: {
        deal_id: dealId,
        user_id: me.id,
        data: isoToday(),
        // A string with two decimal places: a bare JSON number arrives as a float, and
        // `Decimal` then carries only as many places as the float itself.
        ore: '4.00',
        descrizione: 'Ciclo lavoro da fatturare',
        fatturabile: true,
      },
    })
    expect(entry.status(), await entry.text()).toBe(201)
    const moved = await page.request.patch(`/api/deals/${dealId}/stage`, {
      data: { stage_id: vinto.id },
    })
    expect(moved.status(), await moved.text()).toBe(200)

    // And one deal that does *not* satisfy it, so "the filter narrows" is a fact this test
    // owns rather than one it inherits from whatever else the database happens to hold.
    await seedDealWithRate(page.request, {
      nome: `Ciclo non fatturabile ${stamp}`,
      tariffa: '90.000000',
    })

    // The operational tab left the dashboard on 2026-09-08; the signal's count still comes
    // from the API, and the list behind it is what this test compares it with.
    const segnali = (await (await page.request.get('/api/dashboard/operational')).json()) as {
      segnali: { codice: string; conteggio: number }[]
    }
    const cartellino = segnali.segnali.find((s) => s.codice === 'vinto_da_fatturare')
    if (!cartellino) throw new Error('il segnale «Vinto ma da fatturare» non è fra i segnali')
    const sulCartellino = cartellino.conteggio
    expect(sulCartellino).toBeGreaterThanOrEqual(1)

    await page.goto('/app/deal/list?da_fatturare=true')
    await expect(page).toHaveURL(/\/app\/deal\/list\?da_fatturare=true/)
    // Filtered rather than bare: `DataTable`'s own loading indicator is also a `status`,
    // so `getByRole('status')` alone is two elements and a strict-mode violation.
    const bandiera = page.getByRole('status').filter({ hasText: 'Vinto ma da fatturare' })
    await expect(bandiera).toContainText('Solo i deal vinti con ore fatturabili')

    // Criterion 2, as a number. The header row is the `+ 1`; `useDeals` drains every page
    // of the cursor, so this is the whole filtered set and not the first fifty of it.
    const filtrate = page.getByRole('row')
    await expect(filtrate).toHaveCount(sulCartellino + 1)

    // The other half of criterion 2, and the one its quiet failure hides behind: an
    // unfiltered list rendered under a label promising a filtered one would satisfy every
    // assertion above except this one.
    await page.goto('/app/deal/list')
    await expect(page.getByRole('status').filter({ hasText: 'Vinto ma da fatturare' })).toHaveCount(
      0,
    )
    const tutte = page.getByRole('row')
    await expect(async () => {
      expect(await tutte.count()).toBeGreaterThan(sulCartellino + 1)
    }).toPass()
  })

  test('le due schede sono raggiungibili e il periodo è nell’URL', async ({ page }) => {
    await loginAsAdmin(page)
    await ensureSpaceHasWork(page)
    const { da, a } = currentMonth()

    await page.goto(`/app/?tab=economica&da=${da}&a=${a}`)
    await expect(page.getByRole('group', { name: 'Ricavi incassati' })).toBeVisible()

    // The period is still in the address bar, which is the entire point of §4: a screenshot
    // or a shared link of a dashboard with no period is a number with no unit. Re-opened
    // from that URL rather than through `page.reload()`, because the router normalises
    // `/app/` to `/app` and Vite's dev server is configured with a public base of `/app/`
    // — so a reload of the normalised URL lands on the dev server's own "did you mean
    // /app/?" page, which is an artefact of the harness and not of the product.
    // `search.spec.ts` records the same trap from the other direction.
    const condiviso = page.url()
    // `&a=`, not `a=`: the closing bound's parameter name is a suffix of the opening one's,
    // so a bare `a=…` would also be found inside `da=…` and the assertion would hold for a
    // URL carrying only one of the two.
    expect(condiviso).toContain(`da=${da}`)
    expect(condiviso).toContain(`&a=${a}`)
    await page.goto(condiviso.replace('/app?', '/app/?'))
    await expect(page.getByRole('group', { name: 'Ricavi incassati' })).toBeVisible()
    // Two tabs since 2026-09-08: the operational one is gone from the page.
    await expect(page.getByRole('tab')).toHaveCount(2)

    await page.goto('/app/?tab=commerciale')
    await expect(page.getByText('Pipeline per stato')).toBeVisible()
    await expect(page.getByLabel('Dal')).toHaveCount(1)
  })

  test('la scheda economica mostra cassa, grafici e la stima fiscale per intero', async ({
    page,
  }) => {
    await loginAsAdmin(page)
    await ensureSpaceHasWork(page)
    await page.goto('/app/?tab=economica')
    await expect(page.getByRole('group', { name: 'Ricavi incassati' })).toBeVisible()
    await expect(page.getByRole('figure', { name: /Andamento economico/ })).toBeVisible()
    await expect(page.getByRole('figure', { name: /Proiezione economica/ })).toBeVisible()
    // The whole estimate is on this page since 2026-09-09, rather than a link to a screen
    // of its own: the caveat the server sends, and the rows the rates are read from.
    await expect(page.getByRole('note')).toContainText(/stima/i)
    await expect(page.getByText(/^Stima fiscale \d{4}$/)).toBeVisible()
    await expect(page.getByText('Ricavi incassabili')).toBeVisible()
    await expect(page.getByRole('link', { name: /Apri la stima fiscale/i })).toHaveCount(0)
  })
})
