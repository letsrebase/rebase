import { readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test'

// Not a *.spec.ts file on purpose: Playwright's own test-file glob
// (testDir: './e2e' in playwright.config.ts) only ever picks up *.spec.ts, so this
// module is safe to import from every spec here without becoming a "suite" of its
// own with zero tests in it.

export const ADMIN_EMAIL = 'e2e@pigro.it'
export const ADMIN_PASSWORD = 'supersegreta1'

export async function login(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/app/login')
  // Since ORB-172 the login is email-first: the password form is the second way and
  // opens on this button. The e2e admin has a password, so this is its door.
  await page.getByRole('button', { name: /Accedi con la password/ }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Accedi' }).click()
  // Not `/\/app(\/|$)/`, which this once was: that pattern is also satisfied by
  // **/app/login** itself, so it passed for a login that had visibly failed to go
  // anywhere. That is not hypothetical -- it is exactly what hid the post-login
  // redirect race `routes/app/login.tsx` now documents (a correct login landed back on
  // the login form; every spec whose next line is a `page.goto` recovered on the full
  // reload, so only `auth.spec.ts` ever noticed). An assertion that its own failure
  // mode satisfies is not an assertion.
  //
  // And not the `/\/app\/?$/` that replaced it either, which anchored on the end of the
  // string and therefore stopped being true the moment `/app/` grew a `validateSearch`
  // (slice 6: the dashboard's period is in the URL, §4). The landing URL is now
  // `/app?tab=commerciale&da=…&a=…`, so an end-anchored pattern fails for every spec in
  // this suite while the login it is checking has actually succeeded. `(\?|$)` is what
  // admits the search string without re-admitting `/app/login`: after `/app` the next
  // character has to be `?` or nothing, and in `/app/login` it is `l`.
  await expect(page).toHaveURL(/\/app\/?(\?|$)/)
}

export async function loginAsAdmin(page: Page): Promise<void> {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
}

/**
 * Makes sure the space holds work, so `/app/` is the dashboard. Since REB-222 the Home of
 * a space with no customer, deal, time entry or document is the start page, and
 * `e2e-setup.sh` seeds none of the four: a spec that opens the dashboard would pass after
 * `crm.spec.ts` had run and fail on its own (`-g`). One customer, only when there is none,
 * so a run against a stack kept alive does not grow a row per spec.
 */
export async function ensureSpaceHasWork(page: Page): Promise<void> {
  const list = (await (await page.request.get('/api/customers?limit=1')).json()) as { items: unknown[] }
  if (list.items.length > 0) return
  const created = await page.request.post('/api/customers', {
    data: { ragione_sociale: `Home ${Date.now()} Srl` },
  })
  expect(created.status()).toBe(201)
}

/**
 * Signs out through the profile menu at the foot of the sidebar, and waits for the login
 * page.
 *
 * A helper because «Esci» stopped being a button. The UI revision of 2026-09-08 moved it
 * into the profile `DropdownMenu` (`AppShell.tsx`: «who is signed in, the space's
 * settings for an admin, the way out»), so it is a `menuitem` inside a menu that has to
 * be opened first -- and `getByRole('button', { name: 'Esci' })`, which two specs still
 * did, waited thirty seconds for a control that no longer exists in that shape. One
 * place, so the next move of that control is one edit.
 *
 * The menu is opened by its accessible name rather than by the user's initials: the
 * trigger shows an avatar, a name and a role, and all three change with whoever is
 * logged in, while `aria-label="Menu del profilo"` does not.
 */
export async function logout(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Menu del profilo' }).click()
  await page.getByRole('menuitem', { name: 'Esci' }).click()
  await expect(page).toHaveURL(/\/app\/login$/)
}

/** The two collapsible groups of the sidebar, spelled as their headers spell them. */
export type SidebarGroup = 'Vendite' | 'Amministrazione'

/**
 * Navigates through the sidebar, opening the entry's group first when it is closed.
 *
 * The third thing the UI revision of 2026-09-08 changed under these specs: nine flat
 * entries became two groups, and a group is *closed* unless it holds the current route
 * or you opened it yourself (`AppShell.tsx`: `isOpen`). So from the dashboard the
 * «Deal» link is in the DOM's future rather than on the screen, and clicking it waits
 * until the test times out.
 *
 * The header is clicked only when the entry is not already visible: from a page inside
 * the group the entry is there, and clicking the header would *close* the group.
 */
export async function navigate(page: Page, group: SidebarGroup, entry: string): Promise<void> {
  const link = page.getByRole('link', { name: entry, exact: true })
  if (!(await link.isVisible())) {
    await page.getByRole('button', { name: group }).click()
  }
  await link.click()
}

/**
 * Runs one action from a row's «⋯» menu, given what that row is *about*.
 *
 * Another consequence of the UI revision of 2026-09-08 (§4: «behind the ⋯ like every
 * other row action»). Per-row controls used to be buttons with their own accessible
 * names -- `archivia codice interno` -- and are now `menuitem`s inside a
 * `DropdownMenu` whose trigger is labelled «Azioni per <thing>». A spec still clicking
 * the old button waits thirty seconds for a control that no longer exists in that shape.
 *
 * `subject` is the row's own label as the trigger spells it, so the call reads as the
 * sentence a person would say: `rowAction(page, 'Codice interno', 'Archivia')`.
 */
export async function rowAction(page: Page, subject: string, action: string): Promise<void> {
  await page.getByRole('button', { name: `Azioni per ${subject}` }).click()
  await page.getByRole('menuitem', { name: action }).click()
}

/**
 * Types into the Kanban's "Nuovo campo"/customer-form "Etichetta" control one
 * keystroke at a time, the same way `FieldsPanel.test.tsx`'s own
 * `userEvent.type` does and `.fill()` cannot: seeing this as a defect at all
 * requires an event per character, because the bug it exists to catch
 * (task-10-brief.md, and this suite's own README-equivalent in
 * task-10-report.md) only manifests when the label's onChange handler runs once
 * per character instead of once for the whole string. A small inter-keystroke
 * delay, not the library's instant default, is deliberate: it is what a human
 * typing at a keyboard actually produces, and it is the exact gap `.fill()`'s
 * single synthetic event cannot represent at all.
 */
export async function typeLikeAHuman(locator: Locator, text: string): Promise<void> {
  await locator.pressSequentially(text, { delay: 20 })
}

/**
 * Drags a Kanban card (identified by its exact visible name) onto the column
 * whose header reads `stageName`, mirroring dnd-kit's own PointerSensor
 * (KanbanBoard.tsx: `activationConstraint: { distance: 6 }`) with a real
 * mouse-event sequence -- hover, `mousedown`, an intermediate move (so the drag
 * actually *activates* past the 6px threshold before the final jump, rather than
 * risking a single teleport dnd-kit's sensor could read as never having moved),
 * hover the target, `mouseup`.
 *
 * The column is found by its heading text and then narrowed to the *droppable*
 * child specifically (the element `useDroppable`'s own `setNodeRef` is attached
 * to, `KanbanBoard.tsx`'s `border-dashed` div) -- not the whole column wrapper,
 * which also contains the header/count/total and would let `.hover()`'s
 * bounding-box centre land outside the actual drop target on a short column.
 */
export async function dragDealToStage(page: Page, dealName: string, stageName: string): Promise<void> {
  const card = page.getByText(dealName, { exact: true })
  const column = page
    .locator('div.flex.w-72.shrink-0.flex-col')
    .filter({ has: page.getByRole('heading', { name: stageName, exact: true }) })
  const dropZone = column.locator('.border-dashed')

  await card.hover()
  await page.mouse.down()
  const cardBox = await card.boundingBox()
  const dropBox = await dropZone.boundingBox()
  if (!cardBox || !dropBox) throw new Error('carta o colonna non misurabile')
  // One intermediate point, clearly past the 6px activation distance, then the
  // real destination -- both via `page.mouse.move` (not `.hover()`, which jumps
  // in one step) so dnd-kit's own drag-start actually fires before the drop.
  await page.mouse.move(cardBox.x + cardBox.width / 2 + 20, cardBox.y + cardBox.height / 2 + 20)
  await page.mouse.move(dropBox.x + dropBox.width / 2, dropBox.y + dropBox.height / 2, { steps: 10 })
  await page.mouse.up()
}

/**
 * Creates a customer through the real UI (`/app/customers` → "Nuovo cliente"), the
 * same form flow `crm.spec.ts`'s own customer-creation test drives, and returns the
 * generated, timestamp-suffixed name so a caller can find the row it just made
 * without racing any other customer already on screen.
 */
export async function createCustomer(page: Page): Promise<string> {
  const name = `Documenti ${Date.now()}`
  await page.goto('/app/customers')
  await page.getByRole('button', { name: /nuovo cliente/i }).click()
  await page.getByLabel('Ragione sociale').fill(name)
  await page.getByRole('button', { name: 'Salva' }).click()
  await expect(page.getByText(name)).toBeVisible()
  return name
}

// -- Killing and relaunching the real API mid-suite --------------------------
//
// Fix round 1: pulled out of resilience.spec.ts (the only caller before this
// round) so a second spec -- the Kanban board has its own, separate copy of
// "a failed request must not look like an empty list" (routes/app/deal/
// index.tsx's `boardUnavailable`, with no `DataTable` underneath it to inherit
// coverage from) -- can drive the exact same kill-and-restore sequence without
// a second, drifting copy of this machinery. Same defaults apps/web/scripts/
// e2e-env.sh exports, read from `process.env` (inherited from apps/web/
// scripts/e2e.sh, which sources that file before launching `pnpm exec
// playwright test`).

const API_PORT = process.env.PIGROCRM_E2E_API_PORT ?? '8000'
const API_PIDFILE = process.env.PIGROCRM_E2E_API_PIDFILE ?? '/tmp/pigrocrm-e2e-api.pid'
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

async function pingApi(): Promise<boolean> {
  try {
    const response = await fetch(`http://localhost:${API_PORT}/openapi.json`)
    return response.ok
  } catch {
    return false
  }
}

async function waitUntil(condition: () => Promise<boolean>, timeoutMs: number, label: string): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await condition()) return
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`timed out waiting for: ${label}`)
}

/** Reads apps/web/scripts/e2e-setup.sh's own pidfile and sends SIGTERM, then
 *  waits until the API genuinely stops answering -- not a fixed sleep, since
 *  how long a graceful uvicorn shutdown takes is not this suite's to guess.
 *
 *  `PIGROCRM_E2E_API_PIDFILE` is one fixed path, not a per-checkout handle
 *  (projects/pigrocrm/AGENTS.md), so a second `pigrocrm-e2e` run alive on the
 *  same box can replace or reap this pid before this call gets to it. Two
 *  ways that shows up, both tolerated: the pidfile can already be gone --
 *  `e2e-teardown.sh`'s own API step ends in `rm -f "$PIGROCRM_E2E_API_PIDFILE"`
 *  -- so a missing file reads as `pid = 0`, same as an empty one; and `kill`
 *  on a pid the other run already reaped answers `ESRCH`, "no such process".
 *  Neither is a reason to fail the test that called us -- the API this pid
 *  named is dead as far as this process can tell, and the wait below is what
 *  actually proves it, not the swallowed error. `pid > 0` guards the empty/
 *  missing case specifically: `kill(0, …)` is not an error, it broadcasts the
 *  signal to every process in the *caller's* own process group, which here is
 *  this Playwright worker and the shell running it. Every other kill error
 *  still throws. Falling through to the same wait in every case is what makes
 *  the API's absence, not merely the signal's delivery, what the test that
 *  follows can rely on (REB-91). */
export async function killApi(): Promise<void> {
  let pid = 0
  try {
    pid = Number(readFileSync(API_PIDFILE, 'utf-8').trim())
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
  }
  try {
    if (pid > 0) process.kill(pid, 'SIGTERM')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error
  }
  await waitUntil(async () => !(await pingApi()), 10_000, 'API to stop answering')
}

/** Relaunches the same `uv run uvicorn` process apps/web/scripts/e2e-setup.sh
 *  started, detached from this test process so it outlives the Playwright run,
 *  and overwrites the pidfile so apps/web/scripts/e2e-teardown.sh -- which runs
 *  after the whole suite, from a completely different process tree -- kills the
 *  right one at the end. Waits until the API genuinely answers again before
 *  returning, for the identical reason `killApi` waits on the way down.
 *
 *  The pidfile is written to a process-unique temporary path first and moved
 *  into place with `renameSync`, which POSIX guarantees is atomic within one
 *  filesystem: a concurrent `killApi`, in this suite or another `pigrocrm-e2e`
 *  run sharing the same fixed path, always reads either the previous pid whole
 *  or this one whole, never a half-written file (REB-91). The temporary file
 *  is removed in a `finally` so a `renameSync` failure (a read-only or full
 *  `/tmp`) does not leave it behind for nothing to ever reap. */
export async function relaunchApi(): Promise<void> {
  const child = spawn('uv', ['run', 'uvicorn', 'pigrocrm_api.main:app', '--port', API_PORT], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: 'ignore',
    env: process.env,
  })
  child.unref()
  if (child.pid) {
    const tmpPath = `${API_PIDFILE}.${process.pid}.tmp`
    try {
      writeFileSync(tmpPath, String(child.pid))
      renameSync(tmpPath, API_PIDFILE)
    } finally {
      rmSync(tmpPath, { force: true })
    }
  }
  await waitUntil(pingApi, 30_000, 'API to answer again')
}

// -- Seeding a priced deal through the API -----------------------------------

/**
 * Creates a customer, a deal on it, and the deal's own hourly rate, and hands back
 * the two ids.
 *
 * Through the API and not through the UI, deliberately: what `time-tracking.spec.ts`
 * is testing is the *hours* screens, and driving the customer form and the deal form
 * first would make a failure in either of them read as a time-tracking failure. The
 * three UI flows those two forms cover are already asserted, once, in `crm.spec.ts`.
 *
 * Takes an `APIRequestContext` rather than a `Page` so a caller can decide which one
 * it hands over -- and in practice there is only one right answer, which is why it is
 * worth stating: it must be **`page.request`**, after a login, never the top-level
 * `request` fixture the brief's own sample passed. Playwright's `request` fixture is
 * an isolated context with a cookie jar of its own; every endpoint touched here is
 * behind `get_actor`, so that jar's requests all come back 401. `page.request` shares
 * the browser context's cookies, session cookie included.
 *
 * `tariffa` travels as the string it was written as (`"80.000000"`), never as a
 * number: `Numeric(12,6)` is what the column is, and a rate at the sixth decimal
 * place is exactly what a `number` round trip would quietly lose.
 */
export async function seedDealWithRate(
  request: APIRequestContext,
  {
    nome,
    tariffa,
    cliente,
  }: { nome: string; tariffa: string; cliente?: Record<string, string> },
): Promise<{ dealId: string; customerId: string }> {
  const customerResponse = await request.post('/api/customers', {
    // Merged over the default, never replacing it: `time-tracking.spec.ts` needs
    // nothing but a name, and `economics.spec.ts` needs a customer complete enough to
    // be *invoiced* -- `check_party_exportable` and `check_recipient_routing` both run
    // inside `InvoiceService.issue`, before a register number is consumed, and refuse a
    // recipient with no address, no P.IVA or no `codice_sdi`. A second seeding helper
    // for that one difference would be two fixtures to keep in step; an override on the
    // one that exists is the same fixture with more of the record filled in.
    data: { ragione_sociale: `${nome} SRL`, ...cliente },
  })
  expect(customerResponse.status(), await customerResponse.text()).toBe(201)
  const customer = (await customerResponse.json()) as { id: string }

  const dealResponse = await request.post('/api/deals', {
    data: { nome, customer_id: customer.id },
  })
  expect(dealResponse.status(), await dealResponse.text()).toBe(201)
  const deal = (await dealResponse.json()) as { id: string }

  // The rate goes on through its own endpoint rather than as a `tariffa_oraria` on
  // `DealCreate` (which would also accept it): `PUT /api/deals/{id}/rate` is the one
  // the Tariffe screen itself calls, so seeding through it means the fixture and the
  // screen under test are writing the same column the same way.
  const rate = await request.put(`/api/deals/${deal.id}/rate`, {
    data: { tariffa_oraria: tariffa },
  })
  expect(rate.status(), await rate.text()).toBe(204)

  return { dealId: deal.id, customerId: customer.id }
}

/**
 * Seeds `count` customers through the real API, in parallel batches.
 *
 * Through the API and not through the UI: 500 rows via forms would take minutes and
 * would be testing the form, not the palette. Through `page.request` and a relative URL,
 * exactly like `seedDealWithRate` above -- that context shares the browser's cookie jar,
 * so the session `loginAsAdmin` established travels with it, and the Vite dev proxy
 * forwards `/api` the same way it forwards the app's own requests.
 *
 * The prefix has to be distinctive per caller. `playwright.config.ts` runs `workers: 1`
 * against one shared database that is never truncated mid-run, so these rows outlive the
 * spec that made them; a generic term would make an assertion pass or fail depending on
 * which spec ran first.
 */
export async function seedCustomers(page: Page, prefix: string, count: number): Promise<void> {
  const BATCH = 25
  for (let start = 0; start < count; start += BATCH) {
    const size = Math.min(BATCH, count - start)
    const responses = await Promise.all(
      Array.from({ length: size }, (_, offset) =>
        page.request.post('/api/customers', {
          data: { ragione_sociale: `${prefix} ${String(start + offset).padStart(4, '0')} Srl` },
        }),
      ),
    )
    // Checked, never fired and forgotten: a fixture that silently seeded 40 rows instead
    // of 500 would make the truncation assertion pass for the wrong reason, or fail for
    // a reason that has nothing to do with the palette.
    for (const response of responses) {
      expect(response.status(), await response.text()).toBe(201)
    }
  }
}

// -- The commercial cycle: an offer waiting, and an inconsistency to repair ---

/** What `seedCycleFixture` leaves behind, all of it hanging off one deal. */
export interface CycleFixture {
  customerId: string
  customerName: string
  dealId: string
  dealName: string
  /** The offer still `inviata`. Accepting it in the UI is the human half of criterion 15. */
  documentId: string
  documentTitle: string
  /** Already `accettata`, on the same still-open deal: the inconsistency the signal counts. */
  staleDocumentId: string
  partitaIva: string
}

/**
 * One customer with a recognisable VAT number, one deal in an open stage, and **two**
 * offers on that deal: one already `accettata` from a moment when automation A1 was
 * switched off, and one still `inviata` for a human to accept with A1 back on.
 *
 * Two offers on one deal, rather than the brief's one offer on each of two deals, because
 * the criterion asks to watch «offerta accettata, deal non vinto» go **down by one** when
 * the human accepts — and one-offer-per-deal cannot produce that.
 * `DocumentRepository.count_accepted_with_unwon_deal` counts *documents* whose deal is not
 * `won`, so accepting an offer on a deal that A1 then wins is a +1 and a −1 in the same
 * instant: the signal stays flat. Flat is a real and worthwhile assertion (it is what
 * "the automation fired" looks like from the dashboard) but it is not the movement the
 * criterion names. Put both offers on the *same* deal and the arithmetic becomes the real
 * one: the stale offer is already counted, the human's acceptance moves the deal to
 * `vinto`, and the stale offer stops counting. One down — because the deal was repaired,
 * which is the only thing this signal ever asks anyone to do.
 *
 * Seeded through the API for the same reason `seedCustomers` is: driving the customer form,
 * the deal form and the upload dropzone first would make a failure in any of them read as a
 * failure of the cycle. Those flows are asserted, once, in `crm.spec.ts` and
 * `documents.spec.ts`.
 *
 * Everything carries a per-run stamp. `playwright.config.ts` runs `workers: 1,
 * fullyParallel: false` against one database that is never truncated between invocations,
 * so a fixed name or a fixed P.IVA would make the second run of this spec match two rows
 * and fail on the fixture rather than on the product.
 */
export async function seedCycleFixture(page: Page): Promise<CycleFixture> {
  const stamp = Date.now()
  // Eleven digits, the shape `_check_fiscal` requires; the last eleven of the millisecond
  // clock, which is what makes it unique per run.
  const partitaIva = String(stamp).slice(-11)
  // A suffix with no digits in it, deliberately: the only place the fragment searched
  // through the palette can be found is the P.IVA, a column the palette's row never shows.
  const suffix = stamp.toString(36).replace(/[0-9]/g, 'x')
  const customerName = `Ciclo Ingegneria ${suffix} Srl`
  const dealName = `Ciclo rifacimento impianti ${suffix}`
  const documentTitle = `Ciclo offerta impianti ${suffix}`

  const customer = await page.request.post('/api/customers', {
    data: { ragione_sociale: customerName, partita_iva: partitaIva },
  })
  expect(customer.status(), await customer.text()).toBe(201)
  const customerId = ((await customer.json()) as { id: string }).id

  // `/api/pipeline-stages`, not the brief's `/api/pipeline`: the shipped router is the
  // authority, and `kanban.spec.ts` already reads the same path.
  const stages = await page.request.get('/api/pipeline-stages')
  expect(stages.status(), await stages.text()).toBe(200)
  const openStage = ((await stages.json()) as { id: string; code: string | null }[]).find(
    (stage) => stage.code === 'lead',
  )
  if (!openStage) throw new Error('lo stato «lead» non è fra gli stati seminati')

  const deal = await page.request.post('/api/deals', {
    data: {
      nome: dealName,
      customer_id: customerId,
      pipeline_stage_id: openStage.id,
      // A string, never a JSON number: `valore_previsto` is `Numeric(12,2)`, and a float
      // round trip is exactly what the rest of this codebase refuses to do to money.
      valore_previsto: '18000.00',
      probabilita: 60,
    },
  })
  expect(deal.status(), await deal.text()).toBe(201)
  const dealId = ((await deal.json()) as { id: string }).id

  const staleDocumentId = await seedOffer(page, dealId, `Ciclo offerta precedente ${suffix}`)
  // Accepting this one with A1 on would fire the automation and win the deal, which is the
  // opposite of what this row is for. Switch A1 off, accept, switch it back on: that leaves
  // exactly the state the signal exists to detect — an accepted offer whose deal was never
  // moved — and leaves the automation on for the half of the cycle a human drives.
  await setAutomationA1(page, false)
  await setOfferState(page, staleDocumentId, 'accettata')
  await setAutomationA1(page, true)

  const documentId = await seedOffer(page, dealId, documentTitle)

  return {
    customerId,
    customerName,
    dealId,
    dealName,
    documentId,
    documentTitle,
    staleDocumentId,
    partitaIva,
  }
}

/**
 * An offer on `dealId`, created and moved to `inviata` — the one state `accettata` is
 * reachable from, in the server's own state machine and in `OFFER_TRANSITIONS` which
 * mirrors it.
 */
async function seedOffer(page: Page, dealId: string, titolo: string): Promise<string> {
  const created = await page.request.post('/api/documents', {
    data: { deal_id: dealId, tipo: 'offerta', titolo },
  })
  expect(created.status(), await created.text()).toBe(201)
  const documentId = ((await created.json()) as { id: string }).id
  await setOfferState(page, documentId, 'inviata')
  return documentId
}

async function setOfferState(page: Page, documentId: string, stato: string): Promise<void> {
  const response = await page.request.post(`/api/documents/${documentId}/status`, {
    data: { stato },
  })
  expect(response.status(), await response.text()).toBe(200)
}

async function setAutomationA1(page: Page, on: boolean): Promise<void> {
  const response = await page.request.put('/api/automation-config', {
    data: { a1_offerta_accettata_vince_deal: on },
  })
  expect(response.status(), await response.text()).toBe(200)
  // Read back rather than trusted. Everything the fixture is for depends on which way this
  // switch points, and a body the server quietly ignored would seed the opposite state and
  // fail three screens later, somewhere that looks like the product's fault.
  const config = (await response.json()) as { a1_offerta_accettata_vince_deal: boolean }
  expect(config.a1_offerta_accettata_vince_deal).toBe(on)
}
