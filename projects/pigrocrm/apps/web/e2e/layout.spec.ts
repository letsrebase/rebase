import AxeBuilder from '@axe-core/playwright'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * The layout checks that `uishot` cannot make (REB-786, REB-787).
 *
 * `uishot` reports `overflowX` for the *document*, and on this application the document
 * never scrolls sideways: the page is a flex column whose `main` is the scroller, so a
 * layout that spills out of `main` is clipped in silence and `overflowX` still reads 0.
 * What is measured here is `main` itself: a page fits its box when `scrollWidth` does not
 * exceed `clientWidth`, and one pixel over is a clipped control.
 *
 * Phone width is 390, the narrowest the product is held to. The sidebar is a rail of 72px
 * there, so `main` is 318px and every card, form row and table has to live in that.
 */

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1440, height: 900 }

// The month before this one, whose 15th is always in the past (the API refuses a day after
// today), and which no other spec writes to: `calendario.spec.ts` and `days.spec.ts` work
// on the current month, and a day panel with a dozen days on it no longer fits a dialog.
const QUINDICI_SCORSO = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 15)
const MESE = `${QUINDICI_SCORSO.getFullYear()}-${String(QUINDICI_SCORSO.getMonth() + 1).padStart(2, '0')}`
const QUINDICI = `${MESE}-15`

/** A contract with a rate, a worked day in the month before this one and a draft invoice. */
async function seedPages(request: APIRequestContext): Promise<{ contractId: string; invoiceId: string }> {
  const customer = await request.post('/api/customers', {
    data: { ragione_sociale: `Layout E2E ${Date.now()} Srl` },
  })
  expect(customer.status(), await customer.text()).toBe(201)
  const customerId = ((await customer.json()) as { id: string }).id

  const contract = await request.post('/api/contracts', {
    data: {
      customer_id: customerId,
      titolo: 'Consulenza con un titolo abbastanza lungo da andare a capo su un telefono',
      inizio: '2020-01-01',
      tipo_rinnovo: 'nessuno',
      preavviso_disdetta_giorni: 30,
      cadenza_fatturazione: 'mensile',
      politica_spese: { kind: 'non_rimborsabile' },
    },
  })
  expect(contract.status(), await contract.text()).toBe(201)
  const contractId = ((await contract.json()) as { id: string }).id
  const card = await request.post(`/api/contracts/${contractId}/rate-cards`, {
    data: {
      valido_da: '2020-01-01',
      tipo: 'giornaliero',
      importo: '450.00',
      unita: 'giorno',
      frazioni_ammesse: ['1.00', '0.50'],
    },
  })
  expect(card.status(), await card.text()).toBe(201)
  const activate = await request.post(`/api/contracts/${contractId}/state`, { data: { stato: 'attivo' } })
  expect(activate.status(), await activate.text()).toBe(200)

  const day = await request.post('/api/work-units', {
    data: {
      contract_id: contractId,
      data: QUINDICI,
      quantita: '1.00',
      descrizione: 'Attività di consulenza',
      stato: 'lavorato',
    },
  })
  expect(day.status(), await day.text()).toBe(201)

  const invoice = await request.post('/api/invoices', { data: { customer_id: customerId, tipo: 'fattura' } })
  expect(invoice.status(), await invoice.text()).toBe(201)
  return { contractId, invoiceId: ((await invoice.json()) as { id: string }).id }
}

/** `main`'s own scroll width against its box, once the page has settled. */
async function mainOverflow(page: Page): Promise<{ scrollWidth: number; clientWidth: number }> {
  await page.waitForLoadState('networkidle')
  return page.evaluate(() => {
    const main = document.querySelector('main')
    if (main === null) throw new Error('the page has no <main>')
    return { scrollWidth: main.scrollWidth, clientWidth: main.clientWidth }
  })
}

test.describe('the layout at 390px', () => {
  test('the pages that overflowed fit inside main', async ({ page }) => {
    await loginAsAdmin(page)
    const { contractId, invoiceId } = await seedPages(page.request)
    await page.setViewportSize(PHONE)

    const pages = [
      ['the contract', `/app/contracts/${contractId}`, 'Consulenza con un titolo'],
      ['the invoice', `/app/invoices/${invoiceId}`, 'Bozza di fattura'],
      ['the notification settings', '/app/settings/notifications', 'Ore di silenzio'],
      ['the hours register', '/app/hours', 'Ore'],
      ['the calendar', '/app/calendar', 'Calendario'],
    ] as const

    for (const [name, path, heading] of pages) {
      await page.goto(path)
      await expect(page.getByRole('heading', { name: heading, exact: false }).first()).toBeVisible()
      const { scrollWidth, clientWidth } = await mainOverflow(page)
      expect(scrollWidth, `${name} (${path}): main is ${scrollWidth}px wide in a ${clientWidth}px box`).toBeLessThanOrEqual(
        clientWidth,
      )
    }
  })
})

test.describe('the calendar', () => {
  for (const [name, viewport] of [
    ['390px', PHONE],
    ['1440px', DESKTOP],
  ] as const) {
    test(`has no contrast or grid-structure violation at ${name}`, async ({ page }) => {
      await loginAsAdmin(page)
      await page.setViewportSize(viewport)
      await page.goto('/app/calendar')
      await expect(page.getByRole('grid')).toBeVisible()
      await page.waitForLoadState('networkidle')

      const { violations } = await new AxeBuilder({ page })
        .withRules(['color-contrast', 'aria-required-children', 'aria-required-parent'])
        .analyze()
      expect(violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`)).toEqual([])
    })
  }

  test('at 390px a day with something on it is readable, not one letter per line', async ({ page }) => {
    await loginAsAdmin(page)
    const { contractId } = await seedPages(page.request)
    expect(contractId).not.toBe('')
    await page.setViewportSize(PHONE)
    await page.goto(`/app/calendar?mese=${MESE}`)

    const giorno = page.getByRole('button', { name: new RegExp(`^15/${MESE.slice(5)}/${MESE.slice(0, 4)}: .*giornat`) })
    await expect(giorno).toBeVisible()
    // The cell is a row of a list, not a 36px column: wide, and the day's line (the
    // quantity and the customer) is wide too, where in a column it wrapped one letter a line.
    const box = await giorno.boundingBox()
    expect(box?.width ?? 0).toBeGreaterThan(200)
    const customer = await giorno.getByText(/Layout E2E/).first().boundingBox()
    expect(customer?.width ?? 0).toBeGreaterThan(60)
  })
})
