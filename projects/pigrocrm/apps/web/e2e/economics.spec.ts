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
function centesimi(testo: string): number {
  const pulito = testo.replace(/[^\d.,-]/g, '')
  const negativo = pulito.startsWith('-')
  const [intero = '0', decimali = ''] = pulito.replace('-', '').replace(/\./g, '').split(',')
  const valore = Number(intero) * 100 + Number(decimali.padEnd(2, '0').slice(0, 2))
  return negativo ? -valore : valore
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
function riga(dove: Page | Locator, etichetta: string | RegExp): Locator {
  return dove.getByText(etichetta).locator('xpath=..')
}

/** `YYYY-MM` for today, from **local** date parts. Never `toISOString().slice(0, 7)`:
 *  that converts to UTC first, so east of Greenwich late on the last evening of a month
 *  the report would open on the next one and the invoice this spec just issued would
 *  fall outside its own window. Mirrors `lib/dates.ts`'s `toIsoMonth`, restated here for
 *  the reason `centesimi` above is. */
function meseCorrente(oggi: Date): string {
  return `${oggi.getFullYear()}-${String(oggi.getMonth() + 1).padStart(2, '0')}`
}

/**
 * `giorni` calendar days, ending today, that are all guaranteed to fall in **today's own
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
function giorniDelMese(oggi: Date, giorni: number): string[] {
  const mese = meseCorrente(oggi)
  return Array.from({ length: giorni }, (_, indice) => {
    const numero = Math.max(1, oggi.getDate() - (giorni - 1 - indice))
    return `${mese}-${String(numero).padStart(2, '0')}`
  })
}

/** `period_label`'s own long form -- "agosto 2026" -- which is what the description of a
 *  month-grouped invoice line reads. Written out rather than taken from `Intl`, because
 *  it is the *backend's* wording that has to be matched, not the browser's. */
const MESI = [
  'gennaio',
  'febbraio',
  'marzo',
  'aprile',
  'maggio',
  'giugno',
  'luglio',
  'agosto',
  'settembre',
  'ottobre',
  'novembre',
  'dicembre',
]

/** Reads a `Row`'s figure as integer hundredths. */
async function importo(locator: Locator): Promise<number> {
  return centesimi(await locator.innerText())
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

    // Never a literal year anywhere in this file: `InvoiceService._check_issue_date`
    // refuses a `data_emissione` outside the current year, so a hard-coded `2026-03-10`
    // would be a spec that starts failing on the first of January. Everything is derived
    // from `new Date()` -- the hours and the emission land in the same calendar month, and
    // therefore inside the year the annual estimate is read for.
    const oggi = new Date()
    const giorni = giorniDelMese(oggi, 3)
    const mesePerEsteso = `${MESI[oggi.getMonth()]} ${oggi.getFullYear()}`

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
    await page.goto('/app/?tab=economica')
    const ricaviFiscaliPrima = await importo(riga(page, 'Ricavi incassabili'))

    // 2. A deal that can actually be invoiced.
    //
    // `page.request`, never the top-level `request` fixture -- see `seedDealWithRate`'s
    // own docstring: that fixture's cookie jar is a separate one and every call here
    // would come back 401. The customer carries the four address parts, the P.IVA and
    // the `codice_sdi` because `check_party_exportable`/`check_recipient_routing` run
    // inside `issue`, *before* a register number is consumed, and refuse a recipient
    // missing any of them.
    const nome = `Progetto Economia ${Date.now()}`
    const { dealId } = await seedDealWithRate(page.request, {
      nome,
      tariffa: '100.000000',
      cliente: {
        partita_iva: '12345678901',
        codice_sdi: 'ABCDEFG',
        indirizzo: 'Corso Italia 5',
        cap: '00100',
        comune: 'Roma',
        provincia: 'RM',
        nazione: 'IT',
      },
    })

    const me = (await (await page.request.get('/api/auth/me')).json()) as { id: string }
    // Three sessions of eight hours, not one entry of twenty-four: `ck_time_entries_ore_range`
    // caps a single entry at 24 hours (the brief's own sample logged 40 in one row and is
    // refused by the database), and three entries are what makes the *grouping* below
    // observable at all -- one invoice line whose `quantita` is the sum of three rows'
    // `ore` is criterion 5, and it cannot be seen from a single entry.
    //
    // `costo_applicato` explicitly, so the provisional margin further down is a real
    // negative number rather than a zero: twenty-four hours costing 25 EUR/h and not yet
    // invoiced is exactly the state §7.1 insists must not be read as a loss, and a margin
    // of 0,00 € would prove nothing about the qualifier printed beside it.
    for (const giorno of giorni) {
      const voce = await page.request.post('/api/time-entries', {
        data: {
          deal_id: dealId,
          user_id: me.id,
          data: giorno,
          ore: '8.00',
          descrizione: 'Sviluppo',
          costo_applicato: '25.000000',
        },
      })
      expect(voce.status(), await voce.text()).toBe(201)
    }

    // 3. Before any invoice: provisional, and the accrued value is not called revenue.
    await page.goto(`/app/deal/${dealId}`)
    await page.getByRole('tab', { name: 'Economia' }).click()
    await expect(riga(page, 'Ricavi fatturati')).toContainText('0,00 €')
    await expect(riga(page, 'Ricavi fatturati')).toContainText('0 fatture emesse')
    await expect(riga(page, 'Costo del lavoro')).toContainText('600,00 €')
    // The qualifier, and the figure it qualifies. A deal with twenty-four hours and no
    // invoice has a negative margin; that is unfinished work, not a loss.
    await expect(riga(page, 'Margine lordo')).toContainText('-600,00 €')
    await expect(riga(page, 'Margine lordo')).toContainText('(provvisorio)')
    // `null` is not zero per cent: nothing has been invoiced, which is a different
    // sentence from "every euro earned went out in costs".
    await expect(riga(page, 'Margine %')).toContainText('non calcolabile')
    await expect(riga(page, 'Valore maturato')).toContainText('2.400,00 €')
    await expect(riga(page, 'Valore maturato')).toContainText('stima — non è un ricavo')
    await expect(riga(page, 'Ore consuntivate')).toContainText('24')
    await expect(riga(page, 'Ore da fatturare')).toContainText('24')

    // 4. The hours become a draft -- and the deal's economics do not move.
    //
    // This is the single most valuable assertion in this file. An hour bound to a line
    // of a *draft* invoice is `fatturato` to the link-based list filter and is still
    // billable-and-unbilled to every figure below, because a draft is not revenue. The
    // opposite reading would leave the work in neither figure: priced, done, and
    // invisible until somebody pressed «Emetti».
    await page.getByRole('button', { name: 'Genera bozza di fattura' }).click()
    const dialogo = page.getByRole('dialog')
    // The three entries, each priced by the API and none of them summed here.
    await expect(dialogo.getByText('Sviluppo')).toHaveCount(3)
    await expect(dialogo.getByText('800,00 €')).toHaveCount(3)
    await page.getByRole('button', { name: 'Genera', exact: true }).click()
    await expect(page.getByText('Bozza di fattura creata')).toBeVisible()

    await expect(riga(page, 'Ricavi fatturati')).toContainText('0,00 €')
    await expect(riga(page, 'Ricavi fatturati')).toContainText('0 fatture emesse')
    await expect(riga(page, 'Ore da fatturare')).toContainText('24')
    await expect(riga(page, 'Margine lordo')).toContainText('(provvisorio)')
    await expect(riga(page, 'Valore maturato')).toContainText('2.400,00 €')

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
    await expect(page.getByText('Documento emesso')).toBeVisible({ timeout: 120_000 })
    await expect(page.getByText('Emessa')).toBeVisible()

    // Criterion 5, on the document itself: **one** line for three entries, grouped by
    // rate and month, whose `quantita` is the sum of their `ore` -- and whose description
    // names the month it covers, which is what makes it legible beside a timesheet.
    const rigaFattura = page.getByRole('row').filter({ hasText: 'Attività' })
    await expect(rigaFattura).toHaveCount(1)
    await expect(rigaFattura).toContainText(mesePerEsteso)
    await expect(rigaFattura).toContainText('24.00')
    await expect(rigaFattura).toContainText('2.400,00 €')

    // 6. The deal is won, so the margin has the right to stop being provisional.
    //
    // Through `PATCH /api/deals/{id}/stage` -- the endpoint the Kanban drag itself calls
    // -- rather than through the drag: `kanban.spec.ts` already proves that gesture, and
    // a drag failing here would read as an economics failure. `stato` is `chiuso` only
    // when the stage is not `open` *and* nothing billable is left unbilled, so both
    // halves have to be true before the qualifier changes.
    const stages = (await (await page.request.get('/api/pipeline-stages')).json()) as {
      id: string
      code: string | null
    }[]
    const vinto = stages.find((stage) => stage.code === 'vinto')
    expect(vinto, 'lo stato «vinto» deve esistere: e2e-setup.sh semina la pipeline').toBeTruthy()
    const mossa = await page.request.patch(`/api/deals/${dealId}/stage`, {
      data: { stage_id: vinto?.id },
    })
    expect(mossa.status(), await mossa.text()).toBe(200)

    await page.goto(`/app/deal/${dealId}`)
    await page.getByRole('tab', { name: 'Economia' }).click()
    await expect(page.getByText('chiuso', { exact: true })).toBeVisible()
    await expect(riga(page, 'Ricavi fatturati')).toContainText('2.400,00 €')
    await expect(riga(page, 'Ricavi fatturati')).toContainText('1 fatture emesse')
    await expect(riga(page, 'Margine lordo')).toContainText('1.800,00 €')
    await expect(riga(page, 'Margine lordo')).toContainText('(definitivo)')
    await expect(riga(page, 'Margine %')).toContainText('75,00 %')
    await expect(riga(page, 'Ore consuntivate')).toContainText('24')
    await expect(riga(page, 'Ore da fatturare')).toContainText('0')
    // Dropped, not merely zero: once the revenue is the invoice, repeating the estimate
    // beside it invites the reader to treat it as a second figure for the same thing.
    await expect(page.getByText('Valore maturato')).toHaveCount(0)
    // And there is nothing left to bill, so the button that would offer to is gone.
    await expect(page.getByRole('button', { name: 'Genera bozza di fattura' })).toHaveCount(0)

    // 7. The same invoice reaches the annual estimate -- and it says «stima» first.
    //
    // In Home → Economica, under the cards: the estimate is a card of the dashboard since
    // the Analisi section left the interface, read for the year the period on screen
    // names, which for a bare `/app/?tab=economica` is the current one.
    await page.goto('/app/?tab=economica')
    await expect(page.getByRole('note')).toContainText(/stima/i)
    await expect
      .poll(async () => importo(riga(page, 'Ricavi incassabili')))
      .toBe(ricaviFiscaliPrima + 240_000)
    // The three parameters are configured (e2e-setup.sh seeds the forfettario profile),
    // so every derived line is a figure rather than «non calcolabile» -- which is the
    // only way to tell a configured estimate from a missing one.
    // Anchored: `getByText` with a plain string matches a case-insensitive *substring*,
    // and this panel carries both «Aliquota imposta sostitutiva» and «Imposta
    // sostitutiva» -- two rows, one of which is a rate and the other a sum of money.
    await expect(riga(page, /^Imposta sostitutiva/)).not.toContainText('non calcolabile')
    await expect(riga(page, /^Reddito netto stimato/)).not.toContainText('non calcolabile')
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
      nome: `Progetto Errore ${Date.now()}`,
      tariffa: '100.000000',
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
