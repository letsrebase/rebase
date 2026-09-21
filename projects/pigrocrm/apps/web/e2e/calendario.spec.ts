import { expect, test } from '@playwright/test'
import { loginAsAdmin, seedDealWithRate } from './helpers'

/**
 * Slice 10's definition of done, driven the way a person drives it: the menu entry under
 * Home, a day logged from a cell that the hours register then shows, and a deadline
 * created from the same cell.
 *
 * Deliberately not a repeat of the unit tests. What is proven here is that the *screens*
 * connect: a «giornata» written from the calendar is an ordinary time entry -- the same
 * row `/app/hours` lists and the deal's P&L counts -- and a commitment created on a day
 * comes back on that day after a reload, which is the one thing a month assembled from
 * three tables can get wrong.
 */

// The month the calendar opens on, computed the way the page computes it, so this suite
// has no expiry date on it: a literal `2026-09` would start failing in October.
const OGGI = new Date()
const MESE = `${OGGI.getFullYear()}-${String(OGGI.getMonth() + 1).padStart(2, '0')}`
// The first of the month is always in the past or today, which is what `TimeEntryService`
// requires: it refuses a day after today, and the 1st is never after today.
//
// Addressed by the *whole* date and not by the day number, which is the defect the first
// run of this spec found: a month that starts or ends mid-week draws two cells labelled
// «1», so `/^1: /` matched both 1 September and 1 October. The label is
// `gg/mm/aaaa: …`, the same `formatIsoDateItalian` the cell builds it with.
const PRIMO = `01/${String(OGGI.getMonth() + 1).padStart(2, '0')}/${OGGI.getFullYear()}`
const CELLA_PRIMO = new RegExp(`^${PRIMO.replace(/\//g, '\\/')}: `)

test.describe('il calendario', () => {
  test('è nel menu sotto Home e apre sul mese corrente', async ({ page }) => {
    await loginAsAdmin(page)

    // The sidebar entry, which is where the request started: «lo voglio sotto alla home
    // nel menu laterale».
    const link = page.getByRole('link', { name: 'Calendario' })
    await expect(link).toBeVisible()
    await link.click()

    await expect(page).toHaveURL(/\/app\/calendar/)
    await expect(page.getByRole('heading', { level: 1, name: 'Calendario' })).toBeVisible()
    await expect(page.getByRole('grid')).toBeVisible()
  })

  test('il mese sta nell URL, quindi un link a un mese è un link', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto(`/app/calendar?mese=${MESE}`)

    await expect(page.getByRole('grid')).toBeVisible()
    await page.getByRole('button', { name: 'Mese precedente' }).click()
    // The URL carries the month, so the back button and a pasted link both work.
    await expect(page).toHaveURL(/mese=\d{4}-\d{2}/)
  })

  test('una giornata loggata dal calendario è una voce di ore come le altre', async ({
    page,
  }) => {
    await loginAsAdmin(page)
    const nome = `Calendario E2E ${Date.now()}`
    // `page.request`, never the top-level `request` fixture: the second is its own
    // APIRequestContext and carries none of the page's cookies, so every seeding call
    // through it answers 401. `seedDealWithRate`'s own callers all use `page.request`
    // for that reason, and this spec's first run learned it the hard way.
    await seedDealWithRate(page.request, { nome, tariffa: '80.00' })

    await page.goto(`/app/calendar?mese=${MESE}`)
    // The cell is addressed by the label the component builds ("1: … ore"), not by a
    // loose substring: every cell contains a number.
    await page.getByRole('button', { name: CELLA_PRIMO }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('combobox', { name: 'Deal' }).click()
    await page.getByRole('option', { name: nome }).click()
    await dialog.getByRole('button', { name: 'Giornata (8h)' }).click()

    // The calendar itself shows it, which is the read the page just invalidated.
    await expect(dialog.getByText('8.00 h')).toBeVisible()
    await dialog.getByRole('button', { name: 'Chiudi' }).click()

    // And `/app/hours` shows the same row, because it *is* the same row: the calendar
    // fills `POST /api/time-entries`, it does not write the table a second way. The
    // register opens on the current week, which contains the 1st only in the first
    // week of a month -- so the assertion is made through the API, which is the same
    // claim without a week-dependent screen.
    const entries = await page.request.get('/api/time-entries', {
      params: { da: `${MESE}-01`, a: `${MESE}-01` },
    })
    expect(entries.status(), await entries.text()).toBe(200)
    const body = (await entries.json()) as { items: { ore: string; descrizione: string }[] }
    expect(body.items.map((item) => item.ore)).toContain('8.00')
  })

  test('una scadenza creata da un giorno torna su quel giorno', async ({ page }) => {
    await loginAsAdmin(page)
    const titolo = `Scadenza E2E ${Date.now()}`

    await page.goto(`/app/calendar?mese=${MESE}`)
    await page.getByRole('button', { name: CELLA_PRIMO }).click()

    const dialog = page.getByRole('dialog')
    await dialog.getByRole('textbox', { name: 'Titolo della scadenza' }).fill(titolo)
    await dialog.getByRole('button', { name: 'Aggiungi' }).click()
    await expect(dialog.getByText(titolo)).toBeVisible()
    await dialog.getByRole('button', { name: 'Chiudi' }).click()

    // The reload is the point: the month is read again from three tables, and the
    // commitment has to come back on the same day.
    await page.reload()
    await page.getByRole('button', { name: CELLA_PRIMO }).click()
    await expect(page.getByRole('dialog').getByText(titolo)).toBeVisible()
  })

  test('una scadenza si chiude come fatta, e annullarla è un altro bottone', async ({ page }) => {
    await loginAsAdmin(page)
    const titolo = `Da chiudere E2E ${Date.now()}`

    await page.goto(`/app/calendar?mese=${MESE}`)
    await page.getByRole('button', { name: CELLA_PRIMO }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('textbox', { name: 'Titolo della scadenza' }).fill(titolo)
    await dialog.getByRole('button', { name: 'Aggiungi' }).click()
    await expect(dialog.getByText(titolo)).toBeVisible()

    // Two closures, two buttons: «Fatto» records the day, «Annulla» records that it
    // stopped mattering. Neither deletes the row.
    const row = dialog.locator('li', { hasText: titolo })
    await expect(row.getByRole('button', { name: 'Annulla' })).toBeVisible()
    await row.getByRole('button', { name: 'Fatto' }).click()

    // Struck through and without its buttons, still there: the calendar does not hide
    // what was done, or it would read as work that never happened.
    await expect(dialog.getByText(titolo)).toBeVisible()
    await expect(dialog.locator('li', { hasText: titolo }).getByRole('button')).toHaveCount(0)
  })
})
