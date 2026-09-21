import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { loginAsAdmin, rowAction, seedDealWithRate } from './helpers'

/**
 * Plan 4A's own definition of done, driven the way a user drives it. Deliberately not a
 * repeat of the unit tests: what is proven here is that the *screens* connect -- the
 * grid writes an hour the deal tab then shows, a raised rate leaves that hour alone,
 * the timesheet comes out as two real files, and a closed month refuses the next write
 * with the server's own sentence on screen.
 *
 * The brief's own sample imported `test`/`expect` and a `login` fixture from
 * `./fixtures`. No such module exists in this suite and never has: the shared code
 * lives in `./helpers` (a plain module, not a fixtures file -- see its own header for
 * why it is not named `*.spec.ts`), and every other spec here logs in through
 * `loginAsAdmin(page)`. Following the brief literally would have been a second,
 * parallel way to start a test.
 */

// The exact strings `features/time/week.ts` builds its `aria-label`s from -- same
// formatter, same options -- so a cell is addressed by the label it actually carries
// ("Progetto E2E 1756..., lunedì 24 agosto") rather than by a loose substring. The
// brief matched `/Progetto E2E, lun/i` with `.first()`; with the timestamped names this
// suite gives its fixtures (`crm.spec.ts`'s own `ACME ${Date.now()}` convention), an
// exact label is both available and unambiguous, and `.first()` -- which silently picks
// a row when the locator matches several -- is not needed at all.
const GIORNO_LUNGO = new Intl.DateTimeFormat('it-IT', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
})

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

/** The Monday of `value`'s week, at local midnight -- `lib/dates.ts`'s `startOfWeek`,
 *  restated here rather than imported: this file drives the built app from outside it,
 *  and an assertion that shares its arithmetic with the code under test cannot catch
 *  that arithmetic being wrong. */
function lunediDi(value: Date): Date {
  const monday = new Date(value.getFullYear(), value.getMonth(), value.getDate())
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7))
  return monday
}

function piuGiorni(value: Date, giorni: number): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate() + giorni)
}

/**
 * `"1.234,50"` -> `123450`. Integer hundredths, the same reading `lib/decimal.ts` does,
 * so the one place this spec compares two hour totals never goes through a binary float
 * -- and it is written out here rather than imported, for the reason `lunediDi` above is
 * too: a test that borrows the arithmetic of the code it is checking checks nothing.
 */
function centesimi(testo: string): number {
  const [intero = '0', decimali = ''] = testo.replace(/\./g, '').split(',')
  return Number(intero) * 100 + Number(decimali.padEnd(2, '0').slice(0, 2))
}

test.describe('time tracking', () => {
  test('a week of hours, a timesheet and a closed month', async ({ page }) => {
    // Two subprocess renders (pandoc, then Typst) and a dozen navigations: the
    // library's 30s default is not the budget this one test needs.
    test.setTimeout(180_000)

    await loginAsAdmin(page)

    // Timestamped, like every other fixture name in this suite: the grid lists *every*
    // deal, so a fixed name would collide with a previous run's row on any database
    // that outlives one -- and the row it addresses by `aria-label` would then be
    // ambiguous rather than merely duplicated.
    const nome = `Progetto E2E ${Date.now()}`
    // `page.request`, never the top-level `request` fixture -- see `seedDealWithRate`'s
    // own docstring: the fixture's cookie jar is a separate one and every call here
    // would come back 401.
    const { dealId } = await seedDealWithRate(page.request, { nome, tariffa: '80.000000' })

    // Both days this test writes into have to fall in the *same* calendar month: the
    // month it later closes is one month, and a Monday that is the last day of one
    // would put Tuesday in the next. Only the Monday of a week can be a month's last
    // day, and the Monday seven days earlier never is, so stepping the grid back one
    // week is always enough. `weekDays` anchors on "today", so the step has to be made
    // through the screen's own «Settimana precedente» control every time this test
    // returns to it.
    let lunedi = lunediDi(new Date())
    let settimaneIndietro = 0
    if (piuGiorni(lunedi, 1).getMonth() !== lunedi.getMonth()) {
      lunedi = piuGiorni(lunedi, -7)
      settimaneIndietro = 1
    }
    const martedi = piuGiorni(lunedi, 1)
    const anno = lunedi.getFullYear()
    const mese = `${anno}-${String(lunedi.getMonth() + 1).padStart(2, '0')}`
    // `agosto 2026` -- `period_label`'s own long form, which is what both the closed-
    // periods list and the archived report's title read.
    const mesePerEsteso = `${MESI[lunedi.getMonth()]} ${anno}`

    async function apriLaSettimana(): Promise<void> {
      await page.goto('/app/hours')
      // The grid is a tab now. `/app/hours` opens on «Registro» -- the timer bar and the
      // week as a list -- since 2026-09-09, and the grid this spec drives is one click
      // away under «Settimana». Without the click the page is perfectly healthy and
      // `grid-total` simply is not on it, which is how this spec spent three minutes
      // timing out on a screen that works.
      await page.getByRole('tab', { name: 'Settimana' }).click()
      for (let passo = 0; passo < settimaneIndietro; passo += 1) {
        await page.getByRole('button', { name: 'Settimana precedente' }).click()
      }
    }

    const cellaDi = (giorno: Date) =>
      page.getByLabel(`${nome}, ${GIORNO_LUNGO.format(giorno)}`, { exact: true })

    // 1. The weekly grid writes an hour, in the shape an Italian keyboard produces.
    await apriLaSettimana()
    const cella = cellaDi(lunedi)
    const totalePrima = await page.getByTestId('grid-total').innerText()
    await cella.fill('3,5')
    await cella.blur()
    await expect(page.getByTestId(`row-total-${dealId}`)).toHaveText('3,5')
    // The footer too: the row total and the grid total are computed by two different
    // functions over the same cells, and this is the only place they are ever asked to
    // agree against a real API response.
    //
    // As a *delta*, not as `toHaveText('3,5')` -- which the brief asked for and which is
    // only ever true on the first run against a given database. The grid shows every
    // deal, so a second run of this spec against a stack somebody kept alive finds the
    // previous run's five and a half hours already in the same week and reads "9".
    // (Observed exactly that way while writing this.) `apps/web/scripts/e2e.sh` does
    // give every run a brand-new database, so the literal would have held under the one
    // documented command -- and failed for the next person who ran the spec twice by
    // hand, which is the worse of the two ways to be wrong.
    await expect
      .poll(async () => centesimi(await page.getByTestId('grid-total').innerText()))
      .toBe(centesimi(totalePrima) + 350)

    // 2. The deal's Ore tab shows it, with the rate frozen from the deal.
    await page.goto(`/app/deal/${dealId}`)
    await page.getByRole('tab', { name: 'Ore' }).click()
    await expect(page.getByText('80,00 €/h (dal deal)')).toBeVisible()
    // Twice on purpose, and asserted as a count rather than through `.first()`: the
    // figure appears once in the summary card («Valore maturato (stima)», the API's own
    // sum) and once in the row's «Valore» column (the API's own per-row product). Two
    // separate server-side figures that must not disagree; `.first()` would have been
    // satisfied by either one alone. The brief's bare `getByText('280,00 €')` is a
    // strict-mode violation here for exactly this reason.
    await expect(page.getByText('280,00 €')).toHaveCount(2)
    await expect(page.getByText(/Non è un ricavo: il ricavo è la fattura/)).toBeVisible()

    // 3. Raising the deal rate must not move the hour already written (criterion 2,
    //    first half): the rate is copied onto the row when the row is created.
    await page.goto('/app/settings/rates')
    const campoTariffa = page.getByLabel(`Tariffa oraria del deal ${nome}`)
    const rigaTariffa = page.getByRole('row').filter({ has: campoTariffa })
    await campoTariffa.fill('150')
    // Scoped to the row: this screen renders one «Salva» per person and one per deal,
    // so the brief's page-wide `getByRole('button', { name: /salva tariffa/i })` matched
    // nothing at all (the button says «Salva») and a page-wide `/salva/i` would match
    // every row's.
    await rigaTariffa.getByRole('button', { name: 'Salva' }).click()
    await expect(page.getByText('Tariffa del deal aggiornata')).toBeVisible()

    await page.goto(`/app/deal/${dealId}`)
    await page.getByRole('tab', { name: 'Ore' }).click()
    await expect(page.getByText('80,00 €/h (dal deal)')).toBeVisible()
    await expect(page.getByText('280,00 €')).toHaveCount(2)

    // 4. The timesheet comes out in both formats, as real files (criterion 10).
    await page.getByLabel(/rapporto ore del mese/i).fill(mese)

    const scaricamento = page.waitForEvent('download')
    await page.getByRole('link', { name: 'XLSX' }).click()
    const foglio = await scaricamento
    expect(await foglio.suggestedFilename()).toBe(`rapporto-ore-${mese}.xlsx`)
    // A name is not a file. The first four bytes of every .xlsx are a ZIP local file
    // header ("PK\x03\x04"), so this is the cheapest proof that what arrived is a
    // workbook and not an error page with a helpful filename.
    const bytes = readFileSync(await foglio.path())
    expect(bytes.length).toBeGreaterThan(0)
    expect(bytes.subarray(0, 4).toString('latin1')).toBe('PK')

    // The PDF, driven by its own button. It is not an anchor and cannot be one:
    // `GET .../time-report?formato=pdf` archives the report through the slice 2 document
    // pipeline and answers **201 with the document's JSON**, so the button renders,
    // reads that answer, and downloads the archived document through
    // `/api/documents/{id}/download` -- the one path that carries authorisation on
    // either storage backend. Clicking it therefore has to produce a real file *and*
    // leave a document behind, and both halves are asserted here: while the button was
    // still a plain anchor pointed at that JSON, the archived half passed on its own
    // and the file half was the one nobody was making.
    const scaricamentoPdf = page.waitForEvent('download')
    await page.getByRole('button', { name: 'PDF' }).click()
    const rapporto = await scaricamentoPdf
    const pdfBytes = readFileSync(await rapporto.path())
    // "%PDF-". A name is not a file here either, and Typst either ran or it did not.
    expect(pdfBytes.subarray(0, 5).toString('latin1')).toBe('%PDF-')

    await page.getByRole('tab', { name: 'Documenti' }).click()
    await expect(page.getByText(`Rapporto ore ${mesePerEsteso}`)).toBeVisible()

    // 5. A closed month refuses the next write, in the server's own words (criterion 2,
    //    second half). The refusal asserted below is the *API's* -- there is no
    //    client-side guard on the grid at all, which is the point: the toast carries
    //    `toProblem(error).detail`, the exact sentence `PeriodLockService.assert_open`
    //    raised.
    await page.goto('/app/settings/periods')
    await page.getByLabel(/chiudi il mese/i).fill(mese)
    await page.getByRole('button', { name: 'Chiudi periodo' }).click()
    await expect(page.getByText(mesePerEsteso, { exact: true })).toBeVisible()

    await apriLaSettimana()
    const bloccata = cellaDi(martedi)
    await bloccata.fill('2')
    await bloccata.blur()
    await expect(
      page.getByText(/è chiuso: riaprilo per modificare voci datate in quel mese/),
    ).toBeVisible()
    // Refused, not merely complained about: the week is exactly where it was.
    await expect(page.getByTestId(`row-total-${dealId}`)).toHaveText('3,5')

    // ...and an open one does not. Reopening and repeating the identical write is what
    // makes the refusal above attributable to the lock rather than to anything else on
    // that screen -- and it leaves the database as this test found it, so a second run
    // against a surviving database is not met by "il periodo è già chiuso".
    await page.goto('/app/settings/periods')
    // «Riapri» moved behind the row's «⋯» (UI revision of 2026-09-08) and its
    // confirmation stopped being a second button that swapped itself into the row:
    // `PeriodsPanel` asks with a `window.confirm`. Playwright dismisses an unhandled
    // dialog, which for a `confirm` means answering *no* -- so the old two-click
    // sequence waited three minutes for a «Conferma» button that no longer exists, and
    // simply removing it would have reopened nothing. The handler goes in before the
    // click that raises the dialog.
    page.once('dialog', (dialog) => {
      void dialog.accept()
    })
    await rowAction(page, mesePerEsteso, 'Riapri')
    await expect(page.getByText('Nessun periodo chiuso.')).toBeVisible()

    await apriLaSettimana()
    const riaperta = cellaDi(martedi)
    await riaperta.fill('2')
    await riaperta.blur()
    await expect(page.getByTestId(`row-total-${dealId}`)).toHaveText('5,5')
  })

  /**
   * The week grid's own half of the guard `resilience.spec.ts` proves for the list
   * screens: `WeekGrid` renders no table at all when the request failed, because a grid
   * of thirty-five empty cells is precisely the claim -- "nobody worked this week" --
   * that this screen exists to make loudly and must therefore never make by accident.
   */
  test('a failed request never looks like an empty week', async ({ page }) => {
    await loginAsAdmin(page)
    await page.route('**/api/time-entries*', (route) => route.abort('failed'))
    await page.goto('/app/hours')
    // `lib/query.ts` retries a non-401/403 failure twice with backoff before the query
    // is allowed to be an error at all, which outlasts the config's 8s expect timeout on
    // a loaded machine -- the same allowance `resilience.spec.ts` makes for the same
    // policy.
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('grid-total')).toHaveCount(0)
  })
})
