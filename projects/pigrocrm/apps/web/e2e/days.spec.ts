import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * The contract days on screen (REB-745, REB-746, REB-748), driven the way a person drives
 * them: a day recorded from the form, its page, the moves it makes, and its mark on the
 * calendar. The seeding is the API's, because a contract has no screen of its own in this
 * spec: what is proven here is that the day screens connect to the ledger.
 */

const OGGI = new Date()
const ISO = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
const OGGI_ISO = ISO(OGGI)
const MESE = OGGI_ISO.slice(0, 7)
const PRIMO_ISO = `${MESE}-01`
const ITALIANA = (iso: string) => iso.split('-').reverse().join('/')

async function seedContract(
  request: APIRequestContext,
  options: { titolo: string; requiresPriorApproval: boolean },
): Promise<string> {
  const customer = await request.post('/api/customers', { data: { ragione_sociale: `Cliente ${options.titolo}` } })
  expect(customer.status(), await customer.text()).toBe(201)
  const contract = await request.post('/api/contracts', {
    data: {
      customer_id: ((await customer.json()) as { id: string }).id,
      titolo: options.titolo,
      inizio: '2020-01-01',
      tipo_rinnovo: 'nessuno',
      preavviso_disdetta_giorni: 30,
      cadenza_fatturazione: 'mensile',
      politica_spese: { kind: 'non_rimborsabile' },
      requires_prior_approval: options.requiresPriorApproval,
    },
  })
  expect(contract.status(), await contract.text()).toBe(201)
  const id = ((await contract.json()) as { id: string }).id
  const card = await request.post(`/api/contracts/${id}/rate-cards`, {
    data: { valido_da: '2020-01-01', tipo: 'giornaliero', importo: '450.00', unita: 'giorno', frazioni_ammesse: ['1.00', '0.50'] },
  })
  expect(card.status(), await card.text()).toBe(201)
  return id
}

test.describe('le giornate di contratto', () => {
  test('una mezza giornata registrata dal form compare nelle giornate del contratto e sul calendario', async ({ page }) => {
    await loginAsAdmin(page)
    const titolo = `Giornate E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { titolo, requiresPriorApproval: true })

    await page.goto(`/app/days/new?contract=${contractId}`)
    await expect(page.getByText('450,00 €')).toBeVisible()
    await page.getByRole('radio', { name: 'Mezza giornata' }).check({ force: true })
    await page.getByLabel('Cosa hai fatto').fill('Revisione architettura servizi')
    // The contract wants an approval first and none is linked: said before saving.
    await expect(page.getByText(/richiede l’approvazione preventiva/)).toBeVisible()
    await page.getByRole('button', { name: 'Registra giornata' }).click()

    // The day's own page: recorded as worked, flagged because the contract's approval is missing.
    await expect(page).toHaveURL(/\/app\/days\/[0-9a-f-]{36}$/)
    await expect(page.getByRole('heading', { level: 1, name: `Giornata del ${ITALIANA(OGGI_ISO)}` })).toBeVisible()
    await expect(page.getByText('Lavorata senza approvazione').first()).toBeVisible()
    await expect(page.getByText('Mezza giornata')).toBeVisible()

    // The contract's days list it.
    await page.goto(`/app/days?contract=${contractId}`)
    await expect(page.getByRole('link', { name: /Revisione architettura servizi/ })).toBeVisible()

    // And its date on the calendar says it was worked without approval.
    await page.goto(`/app/calendar?mese=${MESE}`)
    const cell = page.getByRole('button', { name: new RegExp(`^${ITALIANA(OGGI_ISO).replace(/\//g, '\\/')}: .*senza approvazione`) })
    await expect(cell).toBeVisible()
    await expect(cell.getByText('senza approvazione')).toBeVisible()
  })

  test('un giorno approvato si segna come lavorato e poi contestato, e la pagina mostra i due passaggi', async ({ page }) => {
    await loginAsAdmin(page)
    const titolo = `Ciclo E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { titolo, requiresPriorApproval: false })
    const created = await page.request.post('/api/work-units', {
      data: { contract_id: contractId, data: PRIMO_ISO, quantita: '1.00', descrizione: 'Workshop con il cliente', stato: 'approvato' },
    })
    expect(created.status(), await created.text()).toBe(201)
    const dayId = ((await created.json()) as { id: string }).id

    await page.goto(`/app/days/${dayId}`)
    await expect(page.getByText('Approvata').first()).toBeVisible()

    await page.getByRole('button', { name: 'Segna come lavorata' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Segna come lavorata' }).click()
    await expect(page.getByText('Lavorata', { exact: true }).first()).toBeVisible()

    await page.getByRole('button', { name: 'Contesta' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('button', { name: 'Contesta' })).toBeDisabled()
    await dialog.getByLabel('Motivo').fill('Il cliente contesta le ore')
    await dialog.getByRole('button', { name: 'Contesta' }).click()

    // Both moves are in the log, each with its reason and a date.
    const history = page.getByRole('region', { name: 'Cronologia' }).or(page.locator('section', { has: page.getByRole('heading', { name: 'Cronologia' }) }))
    await expect(history.getByText('Giornata svolta')).toBeVisible()
    await expect(history.getByText('Il cliente contesta le ore')).toBeVisible()
    await expect(history.locator('time')).toHaveCount(3)
    await expect(page.getByText('Contestata').first()).toBeVisible()
  })

  test('N dalla Home apre il form per registrare una giornata', async ({ page }) => {
    await loginAsAdmin(page)
    await seedContract(page.request, { titolo: `Home E2E ${Date.now()}`, requiresPriorApproval: false })
    await page.goto('/app')
    await expect(page.getByRole('link', { name: /Registra una giornata/ })).toBeVisible()
    await page.keyboard.press('n')
    await expect(page).toHaveURL(/\/app\/days\/new$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Registra una giornata' })).toBeVisible()
  })

  test('l’approvazione di un cliente, con il suo testo come prova, porta i giorni a approvati', async ({ page }) => {
    await loginAsAdmin(page)
    const titolo = `Approvazione E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { titolo, requiresPriorApproval: true })
    const days: string[] = []
    for (const [data, stato] of [
      [PRIMO_ISO, 'proposto'],
      [`${MESE}-02`, 'lavorato'],
    ] as const) {
      const created = await page.request.post('/api/work-units', {
        data: { contract_id: contractId, data, quantita: '1.00', descrizione: `Giornata ${data}`, stato },
      })
      expect(created.status(), await created.text()).toBe(201)
      days.push(((await created.json()) as { id: string }).id)
    }

    // From the contract's own page: its days, and the way to record an approval.
    await page.goto(`/app/contracts/${contractId}`)
    await expect(page.getByRole('heading', { name: 'Giornate' })).toBeVisible()
    await page.getByRole('link', { name: 'Registra approvazione' }).click()
    await expect(page).toHaveURL(/\/app\/approvals\/new\?contract=/)

    await page.getByRole('checkbox', { name: new RegExp(`Giornata ${PRIMO_ISO}`) }).check()
    await page.getByRole('checkbox', { name: new RegExp(`Giornata ${MESE}-02`) }).check()
    await page.getByRole('radio', { name: 'Testo incollato' }).check({ force: true })
    await page.getByLabel('Testo dell’approvazione').fill('Confermo le giornate del mese, il cliente.')
    await page.getByLabel('Da chi').fill('giulia@cliente.example')
    await page.getByLabel(/La frase che approva/).fill('Confermo le giornate del mese')
    await page.getByRole('button', { name: 'Registra approvazione per 2 giornate' }).click()

    // Both days moved: the proposed one is approved, the flagged one is worked again.
    await expect(page).toHaveURL(/\/app\/days\?contract=/)
    await expect(page.getByText('Approvata')).toBeVisible()
    await expect(page.getByText('Lavorata', { exact: true })).toBeVisible()
    await page.goto(`/app/days/${days[1]}`)
    await expect(page.getByText('giulia@cliente.example').first()).toBeVisible()
    await expect(page.getByText('Confermo le giornate del mese').first()).toBeVisible()
  })

  test('chiedere l’approvazione di tre date le registra come proposte e prepara la richiesta', async ({ page }) => {
    await loginAsAdmin(page)
    const titolo = `Richiesta E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { titolo, requiresPriorApproval: true })
    // A customer with an address, so the request has somewhere to go.
    await page.goto(`/app/contracts/${contractId}`)
    await page.getByRole('button', { name: 'Chiedi approvazione' }).click()
    const dialog = page.getByRole('dialog')

    const future = (days: number) => {
      const value = new Date()
      value.setDate(value.getDate() + days)
      return ISO(value)
    }
    for (let index = 0; index < 3; index += 1) {
      if (index > 0) await dialog.getByRole('button', { name: 'Aggiungi una data' }).click()
      await dialog.getByLabel(`Data ${index + 1}`, { exact: true }).fill(future(10 + index))
      await dialog.getByLabel('Cosa farai').nth(index).fill(`Workshop ${index + 1}`)
    }
    await dialog.getByRole('button', { name: 'Leggi la richiesta' }).click()
    await expect(dialog.getByLabel('Messaggio')).toHaveValue(/Workshop 1/)
    await dialog.getByLabel('A', { exact: true }).fill('amministrazione@cliente.example')
    // No mailbox is connected in this stack: the request is prepared as a draft, said so.
    await expect(dialog.getByText(/La posta non è collegata/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Prepara la bozza' }).click()
    await expect(dialog.getByText(/Le 3 giornate sono registrate come proposte/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Chiudi' }).first().click()

    await expect(page.getByRole('link', { name: /Workshop 2/ })).toBeVisible()
    await expect(page.getByText('Proposta').first()).toBeVisible()
  })
})
