import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

const PAGES = ['/', '/pigrocrm', '/privacy', '/terms', '/pitch', '/company'] as const
const BUDGET_BYTES = 40 * 1024
// The hosts these pages may ever talk to besides their own: the ChatGPT Ads measurement
// SDK and PostHog. "May ever" is the whole subtlety -- `consent.js` injects both only
// after a visitor has said yes, so with no decision stored no page requests them at all,
// which is what the first test below checks and the later ones check the other half of.
const PIXEL_HOST = 'bzrcdn.openai.com'
const POSTHOG_HOSTS = ['eu.i.posthog.com', 'eu-assets.i.posthog.com']
const TRACKER_HOSTS = [PIXEL_HOST, ...POSTHOG_HOSTS]
const CONSENT_KEY = 'orbiters.consent'
// The two pages that carry the notice, and therefore the two that can end up with
// a tracker. `src/pixel.test.ts` owns which pages declare it.
const MEASURED_PATHS = ['/', '/pigrocrm'] as const
const towards = (hosts: readonly string[]) => (url: string) => hosts.includes(new URL(url).host)

// REB-349: session.js now asks GET /api/hub/me on every page load. Nothing in this
// suite runs the hub API, so left unmocked every navigation here would hit Vite's
// dev/preview proxy and fail to connect on every single test. A default "signed out"
// answer keeps the rest of the suite isolated from this new network dependency, the
// same way it already is from the hub itself; the describe block below overrides it
// per test to exercise the signed-in cases.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/hub/me', (route) => route.fulfill({ status: 401, body: '' }))
})

test.describe('every page of the site', () => {
  for (const path of PAGES) {
    test(`${path} asks nothing of any host it has not declared`, async ({ page }) => {
      // Every request is collected unconditionally, and only classified as
      // "foreign" once navigation has actually resolved: the very first
      // `request` event IS the navigation to `path` itself, and page.url()
      // still reads as about:blank while it is in flight, so checking the
      // host inline as each event fires misclassifies the page's own load.
      const requested: string[] = []
      page.on('request', (request) => requested.push(request.url()))
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      const host = new URL(page.url()).host
      const foreign = [...new Set(requested.map((url) => new URL(url).host))].filter(
        (requestedHost) => requestedHost !== host,
      )
      // Still a verification and not a promise, and still an exact list rather than an
      // allowance: the two pages an ad lands on may reach the measurement SDK's host
      // and nothing else, and the two legal pages may reach nobody. Before
      // 2026-09-09 every page reached nobody, and this assertion is where that stopped
      // being true -- so it names the one exception instead of being deleted.
      // Before 2026-09-09 every page reached nobody, and that is still true of a first
      // visit: the measurement SDK is not fetched until the notice is answered, so a
      // page that requested it here would mean the consent gate is not a gate.
      expect(foreign).toEqual([])
    })
  }

  test('loads cold under 40 KB, excluding the shared woff2 and the tracker SDKs', async ({
    page,
  }) => {
    let bytes = 0
    page.on('requestfinished', async (request) => {
      if (request.url().endsWith('.woff2')) return
      // Belt and braces: a first visit does not fetch the SDK at all (the notice has
      // not been answered), so this branch only matters if somebody runs this test with
      // consent already stored. Either way the weight of a third party's file is not
      // ours to control, and counting it would make this budget a report on OpenAI's
      // or PostHog's build rather than on our page.
      if (towards(TRACKER_HOSTS)(request.url())) return
      const sizes = await request.sizes()
      bytes += sizes.responseBodySize + sizes.responseHeadersSize
    })
    await page.goto('/', { waitUntil: 'networkidle' })
    expect(bytes, `${bytes} bytes transferred`).toBeLessThan(BUDGET_BYTES)
  })

  for (const path of MEASURED_PATHS) {
    test(`${path} shows the notice, and loads the trackers only once it is accepted`, async ({
      page,
    }) => {
      const requested: string[] = []
      page.on('request', (request) => requested.push(request.url()))
      await page.goto(path)
      await page.waitForLoadState('networkidle')

      const notice = page.locator('.consent')
      await expect(notice).toBeVisible()
      // Nothing has been fetched from OpenAI or PostHog while the question is still open.
      expect(requested.filter(towards(TRACKER_HOSTS))).toEqual([])
      // And both answers are one click away, which is what makes it a consent notice.
      await expect(notice.getByRole('button', { name: 'No' })).toBeVisible()

      await notice.getByRole('button', { name: 'Va bene' }).click()
      await expect(notice).toBeHidden()
      // The pixel's request really does leave now -- its host is unreachable from CI,
      // so what is asserted is the attempt, not a 200. PostHog's does not, and must
      // not: `consent.js` keeps it silent on localhost precisely so this suite never
      // writes a visitor into the project production reports to. That the loader runs
      // on a real host is `consent.test.ts`'s job, on the site's own URL.
      await expect
        .poll(() => requested.filter(towards([PIXEL_HOST])).length)
        .toBeGreaterThan(0)
      // A short fixed wait, not `networkidle`: the pixel's request is towards a host CI
      // cannot reach, and a firewall that drops rather than refuses would keep the
      // network busy until the test's own timeout.
      await page.waitForTimeout(1000)
      expect(requested.filter(towards(POSTHOG_HOSTS))).toEqual([])
      expect(await page.evaluate(() => 'posthog' in window)).toBe(false)
    })

    test(`${path} asks nothing of OpenAI or PostHog after a refusal, and does not ask again`, async ({
      page,
    }) => {
      const requested: string[] = []
      page.on('request', (request) => requested.push(request.url()))
      await page.goto(path)
      await page.locator('.consent').getByRole('button', { name: 'No' }).click()
      await page.reload()
      await page.waitForLoadState('networkidle')

      // A notice that comes back until it gets the answer it wants is a dark pattern
      // with a delay.
      await expect(page.locator('.consent')).toHaveCount(0)
      expect(requested.filter(towards(TRACKER_HOSTS))).toEqual([])
      expect(await page.evaluate((key) => localStorage.getItem(key), CONSENT_KEY)).toBe('denied')
    })
  }

  test('/privacy withdraws consent, so a page that showed the notice shows it again', async ({
    page,
  }) => {
    // Refusing costs one click already (the test above); withdrawing a stored yes has
    // to as well (GDPR art. 7(3)). /privacy carries no tracker of its own and does not
    // load consent.js, so this drives the link privacy.html wires by hand against the
    // same storage key.
    await page.goto('/')
    await page.locator('.consent').getByRole('button', { name: 'Va bene' }).click()
    await expect
      .poll(() => page.evaluate((key) => localStorage.getItem(key), CONSENT_KEY))
      .toBe('granted')

    await page.goto('/privacy')
    await page.getByRole('link', { name: 'Ritira il consenso' }).first().click()
    await page.waitForLoadState('networkidle')
    expect(await page.evaluate((key) => localStorage.getItem(key), CONSENT_KEY)).toBeNull()

    await page.goto('/')
    await expect(page.locator('.consent')).toBeVisible()
  })

  test('shares exactly one font file with the app, from its own origin', async ({ page }) => {
    const fonts: string[] = []
    page.on('request', (request) => {
      if (request.url().endsWith('.woff2')) fonts.push(request.url())
    })
    await page.goto('/', { waitUntil: 'networkidle' })
    expect(fonts).toHaveLength(1)
    expect(fonts[0]).toContain('outfit-variable-latin')
  })

  // The cookie notice on a phone (ORB-18, point 3). It is fixed over the bottom of the
  // viewport, and a short page fits in one screen there, so what it covered stayed
  // covered until the visitor answered. While it is up the page now
  // has the same room under its content, and the room goes when the notice does. 430 is
  // 932 tall here, the height of the phone that width belongs to.
  for (const [width, height] of [
    [360, 844],
    [390, 844],
    [430, 932],
  ] as const) {
    test.describe(`the cookie notice at ${width}x${height}`, () => {
      test.use({ viewport: { width, height }, deviceScaleFactor: 1 })

      /** Where the notice is against what ends the page, scrolled to the very bottom. */
      function geometry() {
        window.scrollTo(0, document.documentElement.scrollHeight)
        const html = document.documentElement
        const body = document.body
        const notice = document.querySelector('.consent')
        const main = document.querySelector('main') as HTMLElement
        const box = document.querySelector('.box') as HTMLElement | null
        const spacer = getComputedStyle(body, '::after')
        // What ends the page in flow: the footer inside main.
        const step = box ? parseFloat(getComputedStyle(box).boxShadow.match(/(-?[\d.]+)px/)?.[1] ?? '0') : 0
        const footer = document.querySelector('body > footer, main > footer')
        const ends = [
          main === box ? main.getBoundingClientRect().bottom + step : (main.lastElementChild as HTMLElement).getBoundingClientRect().bottom,
          footer ? footer.getBoundingClientRect().bottom : -Infinity,
        ]
        return {
          innerHeight: window.innerHeight,
          scrollHeight: html.scrollHeight,
          room: getComputedStyle(html).getPropertyValue('--consent-room').trim(),
          spacer: parseFloat(spacer.height),
          noticeTop: notice ? notice.getBoundingClientRect().top : null,
          noticeHeight: notice ? (notice as HTMLElement).offsetHeight : null,
          contentBottom: Math.max(...ends),
          boxTop: box ? box.getBoundingClientRect().top : null,
          boxBottom: box ? box.getBoundingClientRect().bottom : null,
          // The row ends where the footer starts, or where the spacer does.
          rowEnd: footer
            ? footer.getBoundingClientRect().top
            : window.innerHeight - parseFloat(getComputedStyle(body).paddingBottom) - parseFloat(spacer.height),
          padTop: parseFloat(getComputedStyle(body).paddingTop),
        }
      }

      test('sits under the footer on the landing, and the room goes with a no', async ({ page }) => {
        await page.goto('/', { waitUntil: 'networkidle' })
        const shown = await page.evaluate(geometry)
        expect(shown.noticeTop).not.toBeNull()
        expect(shown.room).toBe(`${Math.ceil(shown.innerHeight - shown.noticeTop!)}px`)
        expect(shown.spacer).toBe(parseFloat(shown.room))
        // The landing scrolls for screens; scrolled to its end, the footer clears the notice.
        expect(shown.contentBottom).toBeLessThanOrEqual(shown.noticeTop!)

        await page.locator('.consent').getByRole('button', { name: 'No' }).click()
        await expect(page.locator('.consent')).toHaveCount(0)
        const after = await page.evaluate(geometry)
        expect(after.room).toBe('')
        expect(after.spacer).toBe(0)
        expect(after.scrollHeight).toBe(shown.scrollHeight - shown.spacer)
      })
    })
  }

  // The title types the roles (ORB-24): "Developer" is what the page ships, then the
  // script deletes it and types the next one, and the rest of the page does not move
  // while it does. The layout half is checked for every word at every width without
  // waiting for the cycle to reach it; the motion half once per page.
  const ROLES = ['Developer', 'AI engineer', 'CTO', 'Fractional CTO', 'Tech lead', 'Freelance']
  const TITLED = ['/'] as const

  // `.role[data-roles]` is the one typed word on the page, wherever it sits: landing.js
  // and system.css find it that way since REB-676, when the hero grew the team builder.
  const CLAIM = '.hero h1'

  /** The tops that must not move, and the claim's height, with `word` in the role. */
  function titledLayout(word: string | null) {
    const role = document.querySelector('.role[data-roles]') as HTMLElement
    if (word !== null) role.textContent = word
    const top = (selector: string) => document.querySelector(selector)!.getBoundingClientRect().top
    return {
      h1: (role.closest('h1, h2') as HTMLElement).getBoundingClientRect().height,
      lead: top('.hero .lead'),
      // The composer and the two doors on the landing, under the typed word.
      form: top('.hero form'),
      doors: top('.hero p.actions'),
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }
  }

  test.describe('the title types the roles', () => {
    for (const path of TITLED) {
      test(`${path} ships "Developer", types another role, and the lead does not move`, async ({
        page,
      }) => {
        await page.goto(path)
        const role = page.locator('.role[data-roles]')
        await expect(page.locator(CLAIM)).toContainText('ma non da soli.')
        await expect(role).toHaveText('Developer')
        expect((await role.getAttribute('data-roles'))!.split('|')).toEqual(ROLES)
        await expect(role).toHaveAttribute('aria-hidden', 'true')
        await expect(role).toHaveClass(/is-typing/)
        const before = await page.evaluate(titledLayout, null)
        // Another full word from the list, once the script has typed it: about three
        // seconds in with the default timings, and never a half-typed one.
        const others = ROLES.filter((word) => word !== 'Developer')
        await expect(role).toHaveText(new RegExp(`^(${others.join('|')})$`), { timeout: 10_000 })
        const after = await page.evaluate(titledLayout, null)
        expect(after.lead).toBe(before.lead)
        expect(after.form).toBe(before.form)
        expect(after.doors).toBe(before.doors)
        expect(after.h1).toBe(before.h1)
      })
    }

    for (const width of [360, 390, 430, 1280]) {
      test.describe(`at ${width} wide`, () => {
        test.use({ viewport: { width, height: 844 }, deviceScaleFactor: 1 })

        for (const path of TITLED) {
          test(`${path} keeps the lead and the form where they are for every role`, async ({
            page,
          }) => {
            await page.goto(path, { waitUntil: 'networkidle' })
            const shipped = await page.evaluate(titledLayout, null)
            for (const word of [...ROLES, '']) {
              const withWord = await page.evaluate(titledLayout, word)
              expect(withWord, `"${word}" on ${path} at ${width}`).toEqual(shipped)
              expect(withWord.scrollWidth).toBe(withWord.innerWidth)
            }
          })
        }
      })
    }

    test.describe('when the reader asked for less motion', () => {
      for (const path of TITLED) {
        test(`${path} keeps the static word, with no cursor`, async ({ page }) => {
          // Per page rather than `test.use({ reducedMotion })`: on this runner the
          // context option did not reach `matchMedia` in the page, and this does.
          await page.emulateMedia({ reducedMotion: 'reduce' })
          await page.goto(path, { waitUntil: 'networkidle' })
          await page.waitForTimeout(4_000)
          const role = page.locator('.role[data-roles]')
          await expect(role).toHaveText('Developer')
          await expect(role).not.toHaveClass(/is-typing/)
          // The line a screen reader hears is the same with or without the script.
          await expect(page.locator(CLAIM)).toHaveAccessibleName(
            'Developer, AI engineer, CTO, ma non da soli.',
          )
          expect(
            await page.evaluate(() =>
              getComputedStyle(document.querySelector('.role[data-roles]')!, '::after').display,
            ),
          ).toBe('none')
        })
      }
    })
  })

  test.describe('with JavaScript disabled', () => {
    test.use({ javaScriptEnabled: false })

    for (const path of PAGES) {
      test(`${path} shows all of its content and all of its links work`, async ({ page }) => {
        await page.goto(path)
        // Nothing on these pages is revealed by a script: every box, card and kicker
        // is visible on the first paint, and the same without any JavaScript at all.
        for (const selector of ['h1', '.box', '.card', '.kicker']) {
          const found = page.locator(selector)
          const count = await found.count()
          for (let index = 0; index < count; index += 1) {
            await expect(found.nth(index)).toBeVisible()
          }
        }
        await expect(page.locator('h1')).toBeVisible()
        for (const link of await page.locator('a[href^="/"]').all()) {
          const href = await link.getAttribute('href')
          expect(href).toBeTruthy()
          // `/hub/` is the rebase hub (projects/hub), another deployable on the same
          // origin: the host's nginx sends it there, the preview server here has nothing
          // behind it. The link is verified where it resolves, not here.
          if (href!.startsWith('/hub/')) continue
          const response = await page.request.get(href!)
          expect(response.status(), `${href} from ${path}`).toBeLessThan(400)
        }
      })
    }
  })
})

// The preview server is what this suite drives, and until ORB-21 it served PigroCRM's
// page at `/` and a 200 for any path at all, so a test written against `/` checked a
// page production never serves there. These pin the served map to deploy/nginx.conf's:
// the same file under each name, the same redirect, and a 404 where nginx has one.

// REB-575: landing.js reads window.__utm, window.__typewriter and window.__pigroField,
// which three other scripts assign. Vite does not promise the order of plain module
// scripts across chunks, so this watches the built bundle itself: an accessor on each
// global logs when it is assigned and when it is read, and no read may come before its
// assignment, whichever chunk Rollup put the assigner in. Unit tests load the source
// with `new Function` and cannot see this.
test('/ defines each window global before landing.js reads it, in the built bundle', async ({ page }) => {
  await page.addInitScript(() => {
    const log: string[] = []
    ;(window as unknown as { __globalsLog: string[] }).__globalsLog = log
    for (const name of ['__utm', '__typewriter', '__pigroField']) {
      let value: unknown
      Object.defineProperty(window, name, {
        configurable: true,
        get() {
          log.push(`read ${name}`)
          return value
        },
        set(next) {
          log.push(`set ${name}`)
          value = next
        },
      })
    }
  })
  await page.goto('/', { waitUntil: 'networkidle' })
  const log = await page.evaluate(() => (window as unknown as { __globalsLog: string[] }).__globalsLog)
  for (const name of ['__utm', '__typewriter', '__pigroField']) {
    expect(log, name).toContain(`set ${name}`)
    expect(log, name).toContain(`read ${name}`)
    expect(log.indexOf(`set ${name}`), `${name} assigned before first read: ${log.join(', ')}`).toBeLessThan(log.indexOf(`read ${name}`))
  }
  // And what the reads were for happened: the field is mounted on its canvas.
  expect(await page.evaluate(() => (document.getElementById('field') as HTMLCanvasElement).width)).toBeGreaterThan(300)
})

// REB-676: the team builder on the first screen. The hub's API is not behind the
// preview, so the one call the page makes is answered here, and what is checked is
// the page's own side: the example fills the box, the call carries the description
// alone, the minimal result is the summary, the roles with their bands and the team's,
// and the door into /hub/team carries the proposal's id, the campaign and da=home like
// every other hub link. A short description never leaves the page, a refusal is read
// in the API's own words, and without JavaScript the form hands the description to
// the hub's page itself.
test.describe('the team builder on the first screen', () => {
  const PROPOSAL = {
    id: '5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd',
    descrizione: 'x',
    persone: null,
    riassunto: 'Una web app per i clienti di una fintech: chi fa il backend e chi il frontend.',
    luogo: { locale: false, dove: null },
    team: [
      {
        posizione: 1,
        ruolo: 'Backend developer',
        motivazione: 'Ha costruito le API di pagamento di due banche.',
        giorni_settimana: 5,
        scheda: { ruolo: 'Sviluppatore backend', seniority: 'senior', anni: 9, competenze: [], settori: [], lingue: [], luogo: null, sintesi: '' },
        modalita: 'remoto',
        fascia: { min: 400, max: 500 },
      },
      {
        posizione: 2,
        ruolo: 'Frontend developer',
        motivazione: 'Porta React.',
        giorni_settimana: 3,
        scheda: { ruolo: 'Sviluppatrice frontend', seniority: 'mid', anni: 4, competenze: [], settori: [], lingue: [], luogo: null, sintesi: '' },
        modalita: 'ibrido',
        fascia: { min: 400, max: 500 },
      },
    ],
    economia: { giorno: { min: 800, max: 1000 }, mese: { min: 17600, max: 22000 }, giorni_mese: 22 },
    previous_id: null,
    origine: 'pubblico',
    created_at: '2026-10-07T08:00:00Z',
  }

  test('an example, «Proponi il team», the minimal result, and the door to the whole team', async ({ page }) => {
    const bodies: unknown[] = []
    await page.route('**/api/hub/team/proposals', async (route) => {
      bodies.push(route.request().postDataJSON())
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROPOSAL) })
    })
    await page.goto('/?utm_source=linkedin&utm_campaign=orbita')
    await page.getByRole('button', { name: 'Web app per una fintech' }).click()
    const box = page.getByLabel('Descrizione del progetto')
    await expect(box).toHaveValue(/fintech/)
    await page.getByRole('button', { name: 'Proponi il team' }).click()
    await expect(page.getByRole('heading', { name: 'La nostra proposta' })).toBeVisible()
    // The example's own headcount travels with the description (three for the fintech).
    expect(bodies).toEqual([{ descrizione: expect.stringContaining('fintech'), persone: 3 }])
    await expect(page.locator('.team-summary')).toHaveText(PROPOSAL.riassunto)
    const members = page.locator('.team-member')
    await expect(members).toHaveCount(2)
    await expect(members.first()).toContainText('Backend developer')
    await expect(members.first()).toContainText('Senior, 9 anni di esperienza')
    await expect(members.first()).toContainText('400–500\u00a0€ al giorno')
    await expect(page.locator('.team-total')).toHaveText('Tutto il team: 800–1.000\u00a0€ al giorno')
    const door = page.getByRole('link', { name: 'Vedi il team completo' })
    const href = new URL((await door.getAttribute('href'))!, 'http://localhost:4173')
    expect(href.pathname).toBe('/hub/team')
    expect(Object.fromEntries(href.searchParams)).toEqual({
      proposta: PROPOSAL.id,
      utm_source: 'linkedin',
      utm_campaign: 'orbita',
      da: 'home',
    })
    // The button is back, for a second description.
    await expect(page.getByRole('button', { name: 'Proponi il team' })).toBeEnabled()
  })

  test('a short description never leaves the page, and a refusal is read in the API’s own words', async ({ page }) => {
    let calls = 0
    await page.route('**/api/hub/team/proposals', async (route) => {
      calls += 1
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Il team builder è spento.' }) })
    })
    await page.goto('/')
    const box = page.getByLabel('Descrizione del progetto')
    await box.fill('Un sito.')
    await page.getByRole('button', { name: 'Proponi il team' }).click()
    await expect(page.getByRole('alert')).toHaveText('Raccontaci qualcosa in più: servono almeno 40 caratteri.')
    expect(calls).toBe(0)
    await box.fill('Rifacciamo il gestionale degli ordini: un backend in Python con FastAPI, sei mesi, da remoto.')
    await page.getByRole('button', { name: 'Proponi il team' }).click()
    await expect(page.getByRole('alert')).toHaveText('Il team builder è spento.')
    expect(calls).toBe(1)
    await expect(page.locator('#team-result')).toBeHidden()
  })

  test.describe('without JavaScript', () => {
    test.use({ javaScriptEnabled: false })

    test('the form hands the description to the hub’s own page', async ({ page }) => {
      await page.goto('/')
      const description = 'Rifacciamo il gestionale degli ordini: un backend in Python con FastAPI, sei mesi, da remoto.'
      await page.getByLabel('Descrizione del progetto').fill(description)
      await page.getByRole('button', { name: 'Proponi il team' }).click()
      // The preview server answers a stand-in for `/hub/`, as it does for every hub path.
      await page.waitForURL(/\/hub\/team\?descrizione=/)
      const url = new URL(page.url())
      expect(url.pathname).toBe('/hub/team')
      expect(url.searchParams.get('descrizione')).toBe(description)
      expect(url.searchParams.get('persone')).toBe('1')
    })
  })
})

// The campaign follows the visitor into the hub (ORB-166): every door on the landing
// carries the six UTM keys the page was opened with, and nothing else does.
test.describe('a visitor from a campaign', () => {
  for (const path of ['/', '/pigrocrm']) {
    test(`${path} carries the UTM keys onto every link into the hub`, async ({ page }) => {
      await page.goto(`${path}?utm_source=linkedin&utm_campaign=orbita&utm_id=42&gclid=nope`, { waitUntil: 'networkidle' })
      const doors = await page.locator('a[href^="/hub/"]').evaluateAll((links) => links.map((a) => a.getAttribute('href')))
      expect(doors.length).toBeGreaterThan(0)
      for (const href of doors) {
        const url = new URL(href!, 'https://letsrebase.com')
        expect(url.searchParams.get('utm_source'), href!).toBe('linkedin')
        expect(url.searchParams.get('utm_campaign'), href!).toBe('orbita')
        expect(url.searchParams.get('utm_id'), href!).toBe('42')
        expect(url.searchParams.has('gclid'), href!).toBe(false)
      }
      // The guide's door keeps its own key beside the campaign's, and every door says
      // which page it is on (ORB-167).
      expect(doors.some((href) => href!.startsWith('/hub/freelance?perk=guida&utm_source=linkedin'))).toBe(true)
      for (const href of doors) expect(new URL(href!, 'https://letsrebase.com').searchParams.get('da'), href!).toBe(path === '/' ? 'home' : 'pigrocrm')
      expect(await page.evaluate(() => sessionStorage.getItem('orbiters.da'))).toBe(path === '/' ? 'home' : 'pigrocrm')
      // The rest of the page's links are what the markup says.
      expect(await page.locator('a[href="/privacy"]').count()).toBeGreaterThan(0)
      expect(await page.evaluate(() => sessionStorage.getItem('orbiters.utm'))).toBe('utm_source=linkedin&utm_campaign=orbita&utm_id=42')
    })
  }

  test('/ without a campaign carries no UTM, only the page', async ({ page }) => {
    await page.goto('/', { waitUntil: 'networkidle' })
    const doors = await page.locator('a[href^="/hub/"]').evaluateAll((links) => links.map((a) => a.getAttribute('href')))
    expect(doors.filter((href) => href!.includes('utm_'))).toEqual([])
    for (const href of doors) expect(new URL(href!, 'https://letsrebase.com').searchParams.get('da'), href!).toBe('home')
    expect(await page.evaluate(() => sessionStorage.getItem('orbiters.utm'))).toBeNull()
    expect(await page.evaluate(() => sessionStorage.getItem('orbiters.da'))).toBe('home')
  })
})

test.describe('the path map, as production serves it', () => {
  const source = (name: string) =>
    readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf-8').match(/<title>([^<]+)<\/title>/)?.[1]

  test('/ is the landing (ORB-145)', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(source('index.html')!)
  })

  test('/orbiters and /community both redirect to /, the community page gone since REB-72', async ({ request }) => {
    for (const path of ['/orbiters', '/community']) {
      const response = await request.get(path, { maxRedirects: 0 })
      expect(response.status(), path).toBe(301)
      expect(response.headers()['location'], path).toBe('/')
    }
  })

  test('/termini redirects to /terms, its name before REB-318', async ({ request }) => {
    const response = await request.get('/termini', { maxRedirects: 0 })
    expect(response.status()).toBe(301)
    expect(response.headers()['location']).toBe('/terms')
  })

  test('/pigrocrm is the CRM\'s own page again (ORB-159)', async ({ page }) => {
    await page.goto('/pigrocrm')
    await expect(page).toHaveTitle(source('pigrocrm.html')!)
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Il CRM che lavora')
  })

  for (const path of ['/nonexistent', '/pigrocrm/', '/index.html', '/community.html']) {
    test(`${path} is a 404, not the landing by fallback`, async ({ page }) => {
      const response = await page.goto(path)
      expect(response?.status()).toBe(404)
    })
  }

  // The image every head points at (ORB-112). A unit test can read the file off disk
  // and the nginx block out of its config; only a server answers whether the two meet,
  // and the same `location /assets/` block serves this card and the hashed bundles.
  test('the share card is served from /assets, as a PNG', async ({ request }) => {
    const response = await request.get('/assets/share-card-2.png')
    expect(response.status()).toBe(200)
    expect(response.headers()['content-type']).toBe('image/png')
  })

  // path-map-plugin.ts's writeBundle hook only runs at the end of a real `vite
  // build`, but the preview server's own middleware answers /robots.txt with the same
  // generated content from memory regardless of what landed on disk (REB-109), so an
  // HTTP request here proves nothing about the build. Reading the file this
  // webServer's own `pnpm build` produced is the only way to prove the hook ran.
  test('robots.txt is served, and the build actually wrote it', async ({ request }) => {
    const response = await request.get('/robots.txt')
    expect(response.status()).toBe(200)
    expect(response.headers()['content-type']).toBe('text/plain; charset=utf-8')
    const built = readFileSync(new URL('../dist/robots.txt', import.meta.url), 'utf-8')
    expect(built).toContain('Sitemap: https://letsrebase.com/sitemap.xml')
  })

  // Same reasoning as robots.txt above: the preview middleware would answer this from
  // memory even if the build never wrote it, so the file this webServer's own build
  // produced is what actually proves the writeBundle hook ran (REB-110).
  test('sitemap.xml is served, lists the indexable pages, and the build actually wrote it', async ({ request }) => {
    const response = await request.get('/sitemap.xml')
    expect(response.status()).toBe(200)
    expect(response.headers()['content-type']).toBe('text/xml; charset=utf-8')
    const built = readFileSync(new URL('../dist/sitemap.xml', import.meta.url), 'utf-8')
    for (const path of ['/', '/pigrocrm', '/privacy', '/terms']) {
      expect(built).toContain(`<loc>https://letsrebase.com${path}</loc>`)
    }
    // /pitch is noindex and stays out of the sitemap, the point of REB-110.
    expect(built).not.toContain('<loc>https://letsrebase.com/pitch</loc>')
  })
})

// The two policy pages are the two whose text column carries long unbreakable strings:
// the Gmail scopes on /privacy are read verbatim by Google's review and cannot be
// shortened. On 2026-09-09 that column was a grid whose one implicit track had grown to
// the widest of them, 452px on a 390px phone, and the page scrolled sideways (ORB-22).
// This asserts the symptom rather than the fix, so the next long string added to
// either page fails here instead of in a visitor's hand.
test.describe('the policy pages on a phone', () => {
  for (const width of [360, 390]) {
    for (const path of ['/privacy', '/terms'] as const) {
      test(`${path} does not scroll sideways at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 })
        await page.goto(path, { waitUntil: 'networkidle' })
        const measured = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }))
        expect(
          measured.scrollWidth,
          `${path} at ${width}px: scrollWidth ${measured.scrollWidth}, clientWidth ${measured.clientWidth}`,
        ).toBe(measured.clientWidth)
      })
    }
  }
})

// REB-349: a visitor who already has a hub session should not have to go through
// /hub/login a second time. session.js asks the hub's own GET /api/hub/me and, only
// on a real answer, points the login link at the person's own area. The hub itself is
// not reachable from this suite, so the answer is mocked at the proxy boundary
// (`/api/hub/me`, the same path the dev and preview servers proxy in production) and
// what is checked is what the visitor's browser does with each answer.
test.describe('Accedi finds a session already there', () => {
  test('/ and /pigrocrm, signed out (a 401): both login links stay put', async ({ page }) => {
    await page.route('**/api/hub/me', (route) => route.fulfill({ status: 401, body: '' }))
    for (const path of ['/', '/pigrocrm'] as const) {
      await page.goto(path, { waitUntil: 'networkidle' })
      const links = await page.locator('a[href^="/hub/login"]').all()
      expect(links.length).toBeGreaterThan(0)
      for (const link of links) {
        expect(await link.getAttribute('href')).toMatch(/^\/hub\/login(\?|$)/)
      }
    }
  })

  test('/privacy and /terms, signed in as a member: Accedi still finds it, with no notice to answer first', async ({
    page,
  }) => {
    await page.route('**/api/hub/me', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ role: 'member' }) }),
    )
    for (const path of ['/privacy', '/terms'] as const) {
      await page.goto(path, { waitUntil: 'networkidle' })
      await expect(page.locator('a[href="/hub/me"]')).toHaveText('Accedi')
    }
  })

  test('/ signed in as a member: both Accedi and "Entra nella tua area" point at /hub/me', async ({ page }) => {
    await page.route('**/api/hub/me', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ role: 'member' }) }),
    )
    await page.goto('/', { waitUntil: 'networkidle' })
    expect(await page.locator('a[href^="/hub/login"]').count()).toBe(0)
    // utm.js has already tagged the link with `?da=home` by the time session.js's
    // answer lands, so the path is what is asserted, not the whole href.
    await expect(page.getByRole('link', { name: 'Accedi' })).toHaveAttribute('href', /^\/hub\/me(\?|$)/)
    await expect(page.getByRole('link', { name: 'Entra nella tua area' })).toHaveAttribute(
      'href',
      /^\/hub\/me(\?|$)/,
    )
  })

  test('/pigrocrm signed in as an admin: Accedi points at /hub/admin', async ({ page }) => {
    await page.route('**/api/hub/me', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ role: 'admin' }) }),
    )
    await page.goto('/pigrocrm', { waitUntil: 'networkidle' })
    await expect(page.getByRole('link', { name: 'Accedi' })).toHaveAttribute('href', /^\/hub\/admin(\?|$)/)
  })
})

// REB-553: the two decks share one script, deck.js, and their stylesheet hides every
// slide until that script marks one `active`. The generic checks above pass on a deck
// that stays blank, so this drives both: the first slide is the one shown, and the
// right arrow moves to the second, in the counter and in the hash alike.
test.describe('the decks', () => {
  for (const path of ['/pitch', '/company'] as const) {
    test(`${path} shows its first slide, and the right arrow moves to the second`, async ({ page }) => {
      await page.goto(path, { waitUntil: 'networkidle' })
      const slides = page.locator('.slide')
      const count = await slides.count()
      expect(count).toBeGreaterThan(1)
      await expect(slides.first()).toHaveClass(/\bactive\b/)
      await expect(slides.first()).toBeVisible()
      await expect(slides.nth(1)).toBeHidden()
      await expect(page.locator('#counter')).toHaveText(`1 / ${count}`)
      await page.keyboard.press('ArrowRight')
      await expect(slides.nth(1)).toHaveClass(/\bactive\b/)
      await expect(slides.first()).not.toHaveClass(/\bactive\b/)
      await expect(page.locator('#counter')).toHaveText(`2 / ${count}`)
      expect(new URL(page.url()).hash).toBe('#2')
    })
  }
})
