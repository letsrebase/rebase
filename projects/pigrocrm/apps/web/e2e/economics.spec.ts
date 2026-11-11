import { expect, test, type Locator, type Page } from '@playwright/test'
import { ensureSpaceHasWork, loginAsAdmin, seedDealWithRate } from './helpers'

/**
 * Plan 4B's own definition of done -- **criterion 12**, the full cycle -- driven the
 * way a human drives it, in one browser, against the real API.
 *
 * What is proven here that no unit test can: that the *screens* of this slice sit on the
 * same rows as its services, and that the one boundary the whole slice turns on survives
 * the trip to the browser. Forty hours are logged, they become a **draft**, and the deal
 * still reports zero revenue and forty hours left to invoice -- a draft is not revenue,
 * which is the invoice-state reading of "already invoiced" that `billed_entry_ids`
 * implements and every figure downstream inherits. Then the same invoice is *issued*
 * from the invoice screen, and the same figures move together on the two screens that
 * still report them: the deal's conto economico and the annual fiscal estimate, which is
 * a card of Home → Economica since the Analisi section left the interface (2026-09-09).
 * The period margins and the estimate-versus-actual report went with it -- their halves
 * of this walk are gone from this file rather than retargeted, because the screens they
 * asserted on no longer exist. `GET /api/analytics/*` still serves both, and
 * `apps/mcp/tests/test_full_cycle.py` reads them the way an agent does.
 *
 * The MCP half of criterion 12 -- an agent preparing the hours and finding no tool to
 * bill them -- is `apps/mcp/tests/test_full_cycle.py`, which drives both adapters over
 * one session. This file is the browser half; between them the claim is complete.
 *
 * The brief's own sample imported `test`/`expect` and a `login` fixture from
 * `./fixtures`. No such module exists in this suite and never has: the shared code lives
 * in `./helpers`, and every other spec here logs in through `loginAsAdmin(page)` -- the
 * same correction `time-tracking.spec.ts` already documents in its own header.
 */

/**
 * `"4.000,00 €"` -> `400000`. Integer hundredths, the same reading `lib/decimal.ts`
 * does, so the two figures this spec compares as a *delta* never go through a binary
 * float -- and written out here rather than imported, for the reason
 * `time-tracking.spec.ts`'s identical helper is: a test that borrows the arithmetic of
 * the code it is checking checks nothing. The euro sign arrives from ICU preceded by a
 * non-breaking space, so everything that is not a digit, a dot or a comma goes first.
 */
function cents(text: string): number {
  const cleaned = text.replace(/[^\d.,-]/g, '')
  const negative = cleaned.startsWith('-')
  const [whole = '0', decimals = ''] = cleaned.replace('-', '').replace(/\./g, '').split(',')
  const value = Number(whole) * 100 + Number(decimals.padEnd(2, '0').slice(0, 2))
  return negative ? -value : value
}

/**
 * The one row of a conto economico (`features/analytics/PnlRows.tsx`'s `Row`, and the
 * identically-shaped one in `features/dashboard/FiscalPanel.tsx`), addressed by its label
 * and read for its value.
 *
 * `getByText` resolves to the *label span* rather than to the row or to any ancestor:
 * Playwright's text engine keeps only the smallest element whose own text matches, and
 * the row `<div>` is excluded because a child of it matches too. `xpath=..` is then the
 * row itself, which is where the figure lives. Addressing the figure directly would be
 * ambiguous -- `0,00 €` is on three rows at once before anything is invoiced, which is
 * exactly the state this spec has the most to say about.
 */
function row(dove: Page | Locator, label: string | RegExp): Locator {
  return dove.getByText(label).locator('xpath=..')
}

/** `YYYY-MM` for today, from **local** date parts. Never `toISOString().slice(0, 7)`:
 *  that converts to UTC first, so east of Greenwich late on the last evening of a month
 *  the report would open on the next one and the invoice this spec just issued would
 *  fall outside its own window. Mirrors `lib/dates.ts`'s `toIsoMonth`, restated here for
 *  the reason `centesimi` above is. */
function monthCurrent(today: Date): string {
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
}

/**
 * `days` calendar days, ending today, that are all guaranteed to fall in **today's own
 * month**: the day number walks backwards from today and is clamped at the first, so on
 * the second of a month it produces `[…-01, …-01, …-02]` rather than stepping into the
 * month before.
 *
 * The clamp matters because everything downstream is read over one span: the hours and
 * the emission both have to land inside the year the annual estimate is read for. A date
 * one month earlier would put the work outside the window that is supposed to contain it,
 * and only on two days out of thirty -- the worst kind of failure to be handed by a
 * suite. Repeating a day is harmless:
 * `time_entries` carries no uniqueness over `(deal, user, data)` by design (§4.1's
 * row-per-entry), which is the same property that lets one person log two sessions in
 * one afternoon.
 */
function daysOfMonth(today: Date, days: number): string[] {
  const month = monthCurrent(today)
  return Array.from({ length: days }, (_, index) => {
    const number = Math.max(1, today.getDate() - (days - 1 - index))
    return `${month}-${String(number).padStart(2, '0')}`
  })
}

/** `period_label`'s own long form -- "agosto 2026" -- which is what the description of a
 *  month-grouped invoice line reads. Written out rather than taken from `Intl`, because
 *  it is the *backend's* wording that has to be matched, not the browser's. */
const Months = [
  'january',
  'february',
  'marzo',
  'aprile',
  'maggio',
  'june',
  'luglio',
  'august',
  'settembre',
  'ottobre',
  'novembre',
  'dicembre',
]

/**
 * Reads a `Row`'s figure as integer hundredths, from the figure span alone: the label span
 * beside it can carry a hint with digits in it («Incassato nel 2026, ...», the basis the
 * server says the estimate was computed on), which `cents` would otherwise read as part of
 * the amount.
 */
async function amount(locator: Locator): Promise<number> {
  return cents(await locator.locator(':scope > span').last().innerText())
}

test.describe('economics', () => {
  test('hours become a draft, the draft becomes revenue, and the margin stops being provisional', async ({
    page,
  }) => {
    // An invoice emission renders a PDF through Typst and an XML through lxml inside the
    // request, and this test then walks five screens. The library's 30s default is not
    // the budget this one test needs -- the same allowance `time-tracking.spec.ts` makes
    // for the same reason.
    test.setTimeout(240_000)

    // Never a literal year anywhere in this file: `invoices.checks.check_issue_date`
    // refuses a `issue_date` outside the current year, so a hard-coded `2026-03-10`
    // would be a spec that starts failing on the first of January. Everything is derived
    // from `new Date()` -- the hours and the emission land in the same calendar month, and
    // therefore inside the year the annual estimate is read for.
    const today = new Date()
    const days = daysOfMonth(today, 3)
    const monthByLongName = `${Months[today.getMonth()]} ${today.getFullYear()}`

    await loginAsAdmin(page)
    await ensureSpaceHasWork(page)

    // 1. What the annual estimate said *before* this test existed.
    //
    // As a delta, not as a literal, and for the reason `time-tracking.spec.ts` states
    // for its own grid total: `apps/web/scripts/e2e.sh` does give every run a brand-new
    // database, so a literal would hold under the one documented command -- and fail for
    // the next person who runs the suite twice by hand against a stack they kept alive.
    // The per-deal assertions further down need no such care: they are scoped to a deal
    // whose name carries this run's own timestamp.
    await page.goto('/app/?tab=economic')
    const revenueTaxBefore = await amount(row(page, 'Ricavi della stima'))

    // 2. A deal that can actually be invoiced.
    //
    // `page.request`, never the top-level `request` fixture -- see `seedDealWithRate`'s
    // own docstring: that fixture's cookie jar is a separate one and every call here
    // would come back 401. The customer carries the four address parts, the P.IVA and
    // the `sdi_code` because `check_party_exportable`/`check_recipient_routing` run
    // inside `issue`, *before* a register number is consumed, and refuse a recipient
    // missing any of them.
    const name = `Progetto Economia ${Date.now()}`
    const { dealId } = await seedDealWithRate(page.request, {
      name,
      rate: '100.000000',
      customer: {
        vat_number: String(Date.now()).slice(-11),
        sdi_code: 'ABCDEFG',
        address: 'Corso Italia 5',
        postal_code: '00100',
        city: 'Roma',
        province: 'RM',
        country: 'IT',
      },
    })

    const me = (await (await page.request.get('/api/auth/me')).json()) as { id: string }
    // Three sessions of eight hours, not one entry of twenty-four: `ck_time_entries_ore_range`
    // caps a single entry at 24 hours (the brief's own sample logged 40 in one row and is
    // refused by the database), and three entries are what makes the *grouping* below
    // observable at all -- one invoice line whose `quantity` is the sum of three rows'
    // `hours` is criterion 5, and it cannot be seen from a single entry.
    //
    // `applied_cost` explicitly, so the provisional margin further down is a real
    // negative number rather than a zero: twenty-four hours costing 25 EUR/h and not yet
    // invoiced is exactly the state §7.1 insists must not be read as a loss, and a margin
    // of 0,00 € would prove nothing about the qualifier printed beside it.
    for (const day of days) {
      const entry = await page.request.post('/api/time-entries', {
        data: {
          deal_id: dealId,
          user_id: me.id,
          date: day,
          hours: '8.00',
          description: 'Sviluppo',
          applied_cost: '25.000000',
        },
      })
      expect(entry.status(), await entry.text()).toBe(201)
    }

    // 3. Before any invoice: provisional, and the accrued value is not called revenue.
    await page.goto(`/app/deal/${dealId}`)
    await page.getByRole('tab', { name: 'Economia' }).click()
    await expect(row(page, 'Ricavi fatturati')).toContainText('0,00 €')
    await expect(row(page, 'Ricavi fatturati')).toContainText('0 fatture emesse')
    await expect(row(page, 'Costo del lavoro')).toContainText('600,00 €')
    // The qualifier, and the figure it qualifies. A deal with twenty-four hours and no
    // invoice has a negative margin; that is unfinished work, not a loss.
    await expect(row(page, 'Margine lordo')).toContainText('-600,00 €')
    await expect(row(page, 'Margine lordo')).toContainText('(provvisorio)')
    // `null` is not zero per cent: nothing has been invoiced, which is a different
    // sentence from "every euro earned went out in costs".
    await expect(row(page, 'Margine %')).toContainText('non calcolabile')
    await expect(row(page, 'Valore maturato')).toContainText('2.400,00 €')
    await expect(row(page, 'Valore maturato')).toContainText('stima — non è un ricavo')
    await expect(row(page, 'Ore consuntivate')).toContainText('24')
    await expect(row(page, 'Ore da fatturare')).toContainText('24')

    // 4. The hours become a draft -- and the deal's economics do not move.
    //
    // This is the single most valuable assertion in this file. An hour bound to a line
    // of a *draft* invoice is `invoiced` to the link-based list filter and is still
    // billable-and-unbilled to every figure below, because a draft is not revenue. The
    // opposite reading would leave the work in neither figure: priced, done, and
    // invisible until somebody pressed «Emetti».
    await page.getByRole('button', { name: 'Genera bozza di fattura' }).click()
    const dialog = page.getByRole('dialog')
    // The three entries, each priced by the API and none of them summed here.
    await expect(dialog.getByText('Sviluppo')).toHaveCount(3)
    await expect(dialog.getByText('800,00 €')).toHaveCount(3)
    await page.getByRole('button', { name: 'Genera', exact: true }).click()
    await expect(page.getByText('Bozza di fattura creata')).toBeVisible()

    await expect(row(page, 'Ricavi fatturati')).toContainText('0,00 €')
    await expect(row(page, 'Ricavi fatturati')).toContainText('0 fatture emesse')
    await expect(row(page, 'Ore da fatturare')).toContainText('24')
    await expect(row(page, 'Margine lordo')).toContainText('(provvisorio)')
    await expect(row(page, 'Valore maturato')).toContainText('2.400,00 €')

    // 5. The human issues it, from the invoice screen, through the real button.
    //
    // `InvoiceActions.onIssue` guards on `window.confirm`, which Playwright *dismisses*
    // by default -- without this handler the click would silently do nothing and the
    // failure would arrive twenty lines later as a missing figure.
    page.on('dialog', (dialog) => void dialog.accept())
    await page.getByRole('tab', { name: 'Fatture' }).click()
    await page.getByRole('row').filter({ hasText: 'Bozza' }).click()
    await expect(page.getByRole('button', { name: 'Emetti' })).toBeVisible()
    await page.getByRole('button', { name: 'Emetti' }).click()
    // `POST /issue` renders the PDF and the XML before it answers (routers/invoices.py:
    // `issue` owns the second transaction), so this one round trip is genuinely slow.
    // Waits on the state, the invoice page's own «Emessa» badge, and not on the success
    // toast: it is transient and its wording («Fattura 2026/1 emessa») is not the
    // sentence this once matched. `exact`, because the toast also contains «emessa» and a
    // substring match would find two elements.
    await expect(page.getByText('Emessa', { exact: true })).toBeVisible({ timeout: 120_000 })

    // Criterion 5, on the document itself: **one** line for three entries, grouped by
    // rate and month, whose `quantity` is the sum of their `hours` -- and whose description
    // names the month it covers, which is what makes it legible beside a timesheet.
    const lineInvoice = page.getByRole('row').filter({ hasText: 'Attività' })
    await expect(lineInvoice).toHaveCount(1)
    await expect(lineInvoice).toContainText(monthByLongName)
    await expect(lineInvoice).toContainText('24.00')
    await expect(lineInvoice).toContainText('2.400,00 €')

    // 6. The deal is won, so the margin has the right to stop being provisional.
    //
    // Through `PATCH /api/deals/{id}/stage` -- the endpoint the Kanban drag itself calls
    // -- rather than through the drag: `kanban.spec.ts` already proves that gesture, and
    // a drag failing here would read as an economics failure. `status` is `closed` only
    // when the stage is not `open` *and* nothing billable is left unbilled, so both
    // halves have to be true before the qualifier changes.
    const stages = (await (await page.request.get('/api/pipeline-stages')).json()) as {
      id: string
      code: string | null
    }[]
    const won = stages.find((stage) => stage.code === 'won')
    expect(won, 'lo stato «vinto» deve esistere: e2e-setup.sh semina la pipeline').toBeTruthy()
    const move = await page.request.patch(`/api/deals/${dealId}/stage`, {
      data: { stage_id: won?.id },
    })
    expect(move.status(), await move.text()).toBe(200)

    await page.goto(`/app/deal/${dealId}`)
    await page.getByRole('tab', { name: 'Economia' }).click()
    await expect(page.getByText('chiuso', { exact: true })).toBeVisible()
    await expect(row(page, 'Ricavi fatturati')).toContainText('2.400,00 €')
    await expect(row(page, 'Ricavi fatturati')).toContainText('1 fatture emesse')
    await expect(row(page, 'Margine lordo')).toContainText('1.800,00 €')
    await expect(row(page, 'Margine lordo')).toContainText('(definitivo)')
    await expect(row(page, 'Margine %')).toContainText('75,00 %')
    await expect(row(page, 'Ore consuntivate')).toContainText('24')
    await expect(row(page, 'Ore da fatturare')).toContainText('0')
    // Dropped, not merely zero: once the revenue is the invoice, repeating the estimate
    // beside it invites the reader to treat it as a second figure for the same thing.
    await expect(page.getByText('Valore maturato')).toHaveCount(0)
    // And there is nothing left to bill, so the button that would offer to is gone.
    await expect(page.getByRole('button', { name: 'Genera bozza di fattura' })).toHaveCount(0)

    // 7. The same invoice reaches the annual estimate once it is collected -- and it says
    // «stima» first.
    //
    // The forfettario is taxed on what was collected in the year (L. 190/2014 art. 1 c. 64),
    // so an issued invoice nobody has paid is no revenue of the estimate yet: it counts the
    // day the payment is recorded, which is what the estimate's own basis line says.
    const issued = (await (
      await page.request.get(`/api/invoices?deal_id=${dealId}&status=issued`)
    ).json()) as { items: { id: string }[] }
    expect(issued.items).toHaveLength(1)
    const paidOn = new Date()
    const paid = await page.request.patch(`/api/invoices/${issued.items[0]!.id}/payment`, {
      data: {
        payment_status: 'paid',
        payment_date: `${paidOn.getFullYear()}-${String(paidOn.getMonth() + 1).padStart(2, '0')}-${String(paidOn.getDate()).padStart(2, '0')}`,
      },
    })
    expect(paid.status(), await paid.text()).toBe(200)
    // In Home → Economica, under the cards: the estimate is a card of the dashboard since
    // the Analisi section left the interface, read for the year the period on screen
    // names, which for a bare `/app/?tab=economic` is the current one.
    await page.goto('/app/?tab=economic')
    await expect(page.getByRole('note')).toContainText(/stima/i)
    await expect
      .poll(async () => amount(row(page, 'Ricavi della stima')))
      .toBe(revenueTaxBefore + 240_000)
    // The three parameters are configured (e2e-setup.sh seeds the forfettario profile),
    // so every derived line is a figure rather than «non calcolabile» -- which is the
    // only way to tell a configured estimate from a missing one.
    // Anchored: `getByText` with a plain string matches a case-insensitive *substring*,
    // and this panel carries both «Aliquota imposta sostitutiva» and «Imposta
    // sostitutiva» -- two rows, one of which is a rate and the other a sum of money.
    await expect(row(page, /^Imposta sostitutiva/)).not.toContainText('non calcolabile')
    await expect(row(page, /^Reddito netto stimato/)).not.toContainText('non calcolabile')
  })

  /**
   * The Economia tab's half of the guard `resilience.spec.ts` proves for the list
   * screens. A conto economico rendered under a failed read is a screenful of dashes and
   * zeroes that look like real figures -- and "this deal made nothing" is precisely the
   * claim this screen exists to make deliberately, so it must never make it by accident.
   */
  test('a failed request never looks like a zero margin', async ({ page }) => {
    await loginAsAdmin(page)
    const { dealId } = await seedDealWithRate(page.request, {
      name: `Progetto Errore ${Date.now()}`,
      rate: '100.000000',
    })
    await page.route('**/api/deals/*/pnl', (route) => route.abort('failed'))
    await page.goto(`/app/deal/${dealId}`)
    await page.getByRole('tab', { name: 'Economia' }).click()
    // `lib/query.ts` retries a non-401/403 failure twice with backoff before the query is
    // allowed to be an error at all, which outlasts the config's 8s expect timeout on a
    // loaded machine -- the same allowance `time-tracking.spec.ts` makes for the same
    // policy.
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/margine/i)).toHaveCount(0)
  })
})
