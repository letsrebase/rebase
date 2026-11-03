import { expect, test, type APIRequestContext } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * The contract days on screen (REB-745, REB-746, REB-748), driven the way a person drives
 * them: a day recorded from the form, its page, the moves it makes, and its mark on the
 * calendar. The seeding is the API's, because a contract has no screen of its own in this
 * spec: what is proven here is that the day screens connect to the ledger.
 */

const TODAY = new Date()
const ISO = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
const TODAY_ISO = ISO(TODAY)
const Month = TODAY_ISO.slice(0, 7)
const FIRST_ISO = `${Month}-01`
const ITALIAN = (iso: string) => iso.split('-').reverse().join('/')

async function seedContract(
  request: APIRequestContext,
  options: { title: string; requiresPriorApproval: boolean },
): Promise<string> {
  const customer = await request.post('/api/customers', { data: { legal_name: `Cliente ${options.title}` } })
  expect(customer.status(), await customer.text()).toBe(201)
  const contract = await request.post('/api/contracts', {
    data: {
      customer_id: ((await customer.json()) as { id: string }).id,
      title: options.title,
      start: '2020-01-01',
      renewal_type: 'none',
      termination_notice_days: 30,
      billing_cadence: 'monthly',
      expense_policy: { kind: 'not_reimbursable' },
      requires_prior_approval: options.requiresPriorApproval,
    },
  })
  expect(contract.status(), await contract.text()).toBe(201)
  const id = ((await contract.json()) as { id: string }).id
  const card = await request.post(`/api/contracts/${id}/rate-cards`, {
    data: { valid_from: '2020-01-01', type: 'daily', amount: '450.00', unit: 'day', allowed_fractions: ['1.00', '0.50'] },
  })
  expect(card.status(), await card.text()).toBe(201)
  return id
}

test.describe('contract days', () => {
  test('a half day recorded from the form shows in the contract\'s days and on the calendar', async ({ page }) => {
    await loginAsAdmin(page)
    const title = `Giornate E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { title, requiresPriorApproval: true })

    await page.goto(`/app/days/new?contract=${contractId}`)
    await expect(page.getByText('450,00 €')).toBeVisible()
    await page.getByRole('radio', { name: 'Mezza giornata' }).check({ force: true })
    await page.getByLabel('Cosa hai fatto').fill('Revisione architettura servizi')
    // The contract wants an approval first and none is linked: said before saving.
    await expect(page.getByText(/richiede l’approvazione preventiva/)).toBeVisible()
    await page.getByRole('button', { name: 'Registra giornata' }).click()

    // The day's own page: recorded as worked, flagged because the contract's approval is missing.
    await expect(page).toHaveURL(/\/app\/days\/[0-9a-f-]{36}$/)
    await expect(page.getByRole('heading', { level: 1, name: `Giornata del ${ITALIAN(TODAY_ISO)}` })).toBeVisible()
    await expect(page.getByText('Lavorata senza approvazione').first()).toBeVisible()
    await expect(page.getByText('Mezza giornata')).toBeVisible()

    // The contract's days list it.
    await page.goto(`/app/days?contract=${contractId}`)
    await expect(page.getByRole('link', { name: /Revisione architettura servizi/ })).toBeVisible()

    // And its date on the calendar says it was worked without approval.
    await page.goto(`/app/calendar?month=${Month}`)
    const cell = page.getByRole('button', { name: new RegExp(`^${ITALIAN(TODAY_ISO).replace(/\//g, '\\/')}: .*senza approvazione`) })
    await expect(cell).toBeVisible()
    await expect(cell.getByText('senza approvazione')).toBeVisible()
  })

  test('an approved day is marked worked then contested, and its page shows the moves', async ({ page }) => {
    await loginAsAdmin(page)
    const title = `Ciclo E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { title, requiresPriorApproval: false })
    // An approved day exists only through an approval, so the fixture records one.
    const created = await page.request.post('/api/work-units', {
      data: { contract_id: contractId, date: FIRST_ISO, quantity: '1.00', description: 'Workshop con il cliente', status: 'proposed' },
    })
    expect(created.status(), await created.text()).toBe(201)
    const dayId = ((await created.json()) as { id: string }).id
    const proof = await page.request.post('/api/documents', {
      data: { contract_id: contractId, type: 'document', title: 'Mail di conferma' },
    })
    expect(proof.status(), await proof.text()).toBe(201)
    const proofId = ((await proof.json()) as { id: string }).id
    const version = await page.request.post(`/api/documents/${proofId}/versions`, {
      multipart: { file: { name: 'conferma.txt', mimeType: 'text/plain', buffer: Buffer.from('Confermo.') } },
    })
    expect(version.status(), await version.text()).toBe(201)
    const approval = await page.request.post('/api/approvals', {
      data: {
        contract_id: contractId,
        channel: 'email',
        sender: 'giulia@cliente.example',
        received_at: new Date().toISOString(),
        excerpt: 'Confermo.',
        document_id: proofId,
        work_unit_ids: [dayId],
      },
    })
    expect(approval.status(), await approval.text()).toBe(201)

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
    await expect(history.locator('time')).toHaveCount(4)
    await expect(page.getByText('Contestata').first()).toBeVisible()
  })

  test('N on the Home opens the form to record a day', async ({ page }) => {
    await loginAsAdmin(page)
    await seedContract(page.request, { title: `Home E2E ${Date.now()}`, requiresPriorApproval: false })
    await page.goto('/app')
    await expect(page.getByRole('link', { name: /Registra una giornata/ })).toBeVisible()
    await page.keyboard.press('n')
    await expect(page).toHaveURL(/\/app\/days\/new$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Registra una giornata' })).toBeVisible()
  })

  test('a client\'s approval with pasted text as its proof moves the days it covers', async ({ page }) => {
    await loginAsAdmin(page)
    const title = `Approvazione E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { title, requiresPriorApproval: true })
    const days: string[] = []
    for (const [date, status] of [
      [FIRST_ISO, 'proposed'],
      [`${Month}-02`, 'worked'],
    ] as const) {
      const created = await page.request.post('/api/work-units', {
        data: { contract_id: contractId, date, quantity: '1.00', description: `Giornata ${date}`, status },
      })
      expect(created.status(), await created.text()).toBe(201)
      days.push(((await created.json()) as { id: string }).id)
    }

    // From the contract's own page: its days, and the way to record an approval.
    await page.goto(`/app/contracts/${contractId}`)
    await expect(page.getByRole('heading', { name: 'Giornate', exact: true })).toBeVisible()
    await page.getByRole('link', { name: 'Registra approvazione' }).click()
    await expect(page).toHaveURL(/\/app\/approvals\/new\?contract=/)

    await page.getByRole('checkbox', { name: new RegExp(`Giornata ${FIRST_ISO}`) }).check()
    await page.getByRole('checkbox', { name: new RegExp(`Giornata ${Month}-02`) }).check()
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

  test('asking for approval of three dates records them as proposed and prepares the request', async ({ page }) => {
    await loginAsAdmin(page)
    const title = `Richiesta E2E ${Date.now()}`
    const contractId = await seedContract(page.request, { title, requiresPriorApproval: true })
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
