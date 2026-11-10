/* The cookie notice, driven in a DOM.
 *
 * @vitest-environment-options { "url": "https://letsrebase.com/" }
 *
 * On the site's own host, not jsdom's default `localhost`: PostHog is silent on
 * localhost by design (see `measured` below), and these tests are about what a visitor's
 * browser does.
 *
 * The property that matters is not that a box appears: it is that **nothing is fetched
 * before somebody says yes**. So most of these tests assert the absence of a script
 * element, which is the only thing a visitor's browser would actually do differently.
 * `pixel.test.ts` holds the static half -- that no page carries a pixel of its own, so
 * this file is the whole of the gate.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CONSENT_STORAGE_KEY } from '@rebase/analytics/consent'
import { POSTHOG_ASSET_HOST, POSTHOG_HOST, POSTHOG_KEY } from '@rebase/analytics'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const js = readFileSync(join(__dirname, 'consent.js'), 'utf-8')
const withdrawJs = readFileSync(join(__dirname, 'consent-withdraw.js'), 'utf-8')
const revealGateJs = readFileSync(join(__dirname, 'public/assets/reveal-gate.js'), 'utf-8')
const SDK_URL = 'https://bzrcdn.openai.com/sdk/oaiq.min.js'
const POSTHOG_URL = `${POSTHOG_ASSET_HOST}/static/array.js`
const KEY = 'rebase.consent'
const LEGACY_KEY = 'orbiters.consent'

type Consent = {
  start: () => void
  decide: (decision: string, box?: Element | null) => void
  measured: (hostname: string) => boolean
  internal: (hostname: string) => boolean
  STORAGE_KEY: string
}

function run(): Consent {
  new Function(js)()
  return (window as unknown as { __consent: Consent }).__consent
}

function notice(): HTMLElement | null {
  return document.querySelector('.consent')
}

function sdkScripts(): HTMLScriptElement[] {
  return [...document.querySelectorAll('script')].filter((script) => script.src === SDK_URL)
}

function posthogScripts(): HTMLScriptElement[] {
  return [...document.querySelectorAll('script')].filter((script) => script.src === POSTHOG_URL)
}

type Stubs = { oaiq?: unknown; posthog?: unknown }
const stubs = (): Stubs => window as unknown as Stubs

function forget(): void {
  delete stubs().oaiq
  delete stubs().posthog
}

function press(label: string): void {
  const button = [...document.querySelectorAll('.consent button')].find(
    (candidate) => candidate.textContent === label,
  )
  expect(button, `nessun bottone "${label}"`).toBeTruthy()
  ;(button as HTMLButtonElement).click()
}

const realLocation = window.location

/** The page's `window.location` with some members replaced: another host, a `reload` to spy on. */
function stubLocation(overrides: Record<string, unknown>): void {
  Object.defineProperty(window, 'location', {
    value: { ...realLocation, hostname: realLocation.hostname, ...overrides },
    writable: true,
    configurable: true,
  })
}

/** Runs the withdraw script on a page with one "Ritira il consenso" link and clicks it.
 *  Returns whether the browser's own navigation was left to happen (it must not be). */
function clickWithdraw(): boolean {
  document.body.innerHTML = '<a href="/privacy" data-consent-withdraw>Ritira il consenso</a>'
  new Function(withdrawJs)()
  const click = new MouseEvent('click', { bubbles: true, cancelable: true })
  document.querySelector('[data-consent-withdraw]')?.dispatchEvent(click)
  return !click.defaultPrevented
}

function clearCookies(): void {
  for (const pair of document.cookie.split(';')) {
    const name = pair.split('=')[0]?.trim()
    if (!name) continue
    for (const domain of ['', '; Domain=letsrebase.com', '; Domain=.letsrebase.com']) {
      document.cookie = `${name}=; Max-Age=0; Path=/${domain}`
    }
  }
}

beforeEach(() => {
  document.body.innerHTML = ''
  document.head.innerHTML = ''
  // The room the notice asks for is written on the root element, which the two lines
  // above do not touch.
  document.documentElement.removeAttribute('style')
  window.localStorage.clear()
  forget()
})

afterEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  clearCookies()
  Object.defineProperty(window, 'location', { value: realLocation, writable: true, configurable: true })
})

describe('before anyone has decided', () => {
  it('shows one small notice and loads nothing', () => {
    run()
    expect(notice()).toBeTruthy()
    // The whole point. A snippet in the head with `consent(false)` after it would have
    // fetched this script anyway and handed OpenAI the visitor's IP.
    expect(sdkScripts()).toHaveLength(0)
    expect(posthogScripts()).toHaveLength(0)
    expect(stubs().oaiq).toBeUndefined()
    expect(stubs().posthog).toBeUndefined()
  })

  it('asks in one sentence, and links the page that explains it', () => {
    run()
    const box = notice() as HTMLElement
    // Short is the requirement, not a sentence count: a notice nobody reads is a
    // notice that failed, and one that covers the page is worse than none.
    expect((box.textContent ?? '').length).toBeLessThan(130)
    expect(box.querySelector('a')?.getAttribute('href')).toBe('/privacy')
    expect(box.getAttribute('role')).toBe('region')
    expect(box.getAttribute('aria-label')).toBe('Cookie e misurazione')
  })

  it('offers a no as plainly as a yes', () => {
    // Both answers are one click from here. A notice whose only button is "accept" is
    // not a consent mechanism.
    run()
    const labels = [...document.querySelectorAll('.consent button')].map(
      (button) => button.textContent,
    )
    expect(labels).toEqual(['No', 'Va bene'])
  })
})

describe('once somebody accepts', () => {
  it('loads the SDK, initialises the pixel, and remembers the yes', () => {
    run()
    press('Va bene')

    const scripts = sdkScripts()
    expect(scripts).toHaveLength(1)
    expect(scripts[0]?.async).toBe(true)
    expect(window.localStorage.getItem(KEY)).toBe('granted')
    // The queue exists so a `measure` call made before the SDK arrives is not lost, and
    // the init is the first thing in it. Copied through `Array.from` because the stub
    // pushes the real `arguments` object, which `toEqual` does not consider an array,
    // and asserted whole so an empty queue fails here rather than on an index read.
    const queued = (window as unknown as { oaiq: { q: unknown[][] } }).oaiq.q
    expect(Array.from(queued[0] ?? [])).toEqual([
      'init',
      { pixelId: '9r6qrnPxBV8WDVGtpuaqxh' },
    ])
  })

  it('loads PostHog with the same click, from the EU host, anonymous until a login', () => {
    run()
    press('Va bene')

    const scripts = posthogScripts()
    expect(scripts).toHaveLength(1)
    expect(scripts[0]?.async).toBe(true)
    // The stub `array.js` reads on arrival: `_i` holds the one init, `__SV` marks it as
    // a stub, and anything called before the SDK lands is queued on the array itself.
    const stub = stubs().posthog as unknown[] & {
      __SV: number
      _i: [string, Record<string, unknown>, undefined][]
      capture: (name: string) => void
    }
    expect(stub.__SV).toBe(1)
    expect(stub._i).toHaveLength(1)
    const [key, config] = stub._i[0] ?? []
    expect(key).toMatch(/^phc_/)
    // The project and hosts are the ones every other surface reads from
    // `shared/analytics`; consent.js repeats them as literals because it has no bundler.
    expect(key).toBe(POSTHOG_KEY)
    expect(scripts[0]?.src).toBe(POSTHOG_URL)
    expect(config).toMatchObject({
      api_host: POSTHOG_HOST,
      person_profiles: 'identified_only',
      session_recording: { maskAllInputs: true },
    })
    stub.capture('iscrizione_community')
    // letsrebase.com is a visitor's host, so nothing was queued before this call.
    expect(stub[0]).toEqual(['capture', 'iscrizione_community'])
    // The preview stacks would have queued `setInternalOrTestUser` first: the rule is
    // the same as `shared/analytics`'s, and it is applied where the init is.
    const { internal } = (window as unknown as { __consent: Consent }).__consent
    expect(internal('preview.letsrebase.com')).toBe(true)
    expect(internal('preview.pigro.letsrebase.com')).toBe(true)
    expect(internal('letsrebase.com')).toBe(false)
    expect(internal('www.letsrebase.com')).toBe(false)
  })

  it('marks the preview stacks as internal, where a visitor\'s host queues nothing of the kind', () => {
    stubLocation({ hostname: 'preview.letsrebase.com' })
    run()
    press('Va bene')
    const stub = stubs().posthog as unknown[]
    expect(stub[0]).toEqual(['setInternalOrTestUser'])
  })

  it('takes the notice away and does not ask again on the next page', () => {
    run()
    press('Va bene')
    expect(notice()).toBeNull()

    document.body.innerHTML = ''
    document.head.innerHTML = ''
    forget()
    run()
    expect(notice()).toBeNull()
    // And both trackers are there without being asked for a second time.
    expect(sdkScripts()).toHaveLength(1)
    expect(posthogScripts()).toHaveLength(1)
  })
})

describe('withdrawing consent', () => {
  it('clears the stored decision, reloads, and the next run shows the notice again', () => {
    // GDPR art. 7(3): withdrawing has to cost the same one click as consenting.
    // `decide('granted')` already puts the visitor where a stored yes would; this is
    // the transition back out of it.
    const consent = run()
    press('Va bene')
    expect(window.localStorage.getItem(consent.STORAGE_KEY)).toBe('granted')
    // The website and the hub read the decision under the one key `shared/analytics`
    // names, and the withdraw script clears that same key.
    expect(consent.STORAGE_KEY).toBe(CONSENT_STORAGE_KEY)

    const reload = vi.fn()
    stubLocation({ reload })
    expect(clickWithdraw()).toBe(false)

    expect(window.localStorage.getItem(KEY)).toBeNull()
    expect(reload).toHaveBeenCalledTimes(1)

    // The reload itself: a fresh run(), the same way `once somebody accepts` simulates
    // the next page load above.
    document.body.innerHTML = ''
    document.head.innerHTML = ''
    forget()
    run()
    expect(notice()).toBeTruthy()
    expect(sdkScripts()).toHaveLength(0)
    expect(posthogScripts()).toHaveLength(0)
  })

  it('also takes away what the trackers left behind: ph_* and __obref cookies, ph_* storage', () => {
    run()
    press('Va bene')
    // Set the way the SDKs set them: on the top-level domain, and PostHog's also on the host.
    document.cookie = 'ph_phc_abc_posthog=1; Domain=.letsrebase.com; Path=/'
    document.cookie = '__obref=click; Domain=.letsrebase.com; Path=/'
    document.cookie = 'ph_other=1; Path=/'
    document.cookie = 'keep=1; Path=/'
    window.localStorage.setItem('ph_phc_abc_posthog', '{}')
    window.localStorage.setItem('unrelated', '1')
    window.sessionStorage.setItem('ph_session_id', 'x')
    window.sessionStorage.setItem('unrelated', '1')
    expect(document.cookie).toContain('ph_phc_abc_posthog')

    stubLocation({ reload: vi.fn() })
    clickWithdraw()

    expect(document.cookie).toBe('keep=1')
    expect(window.localStorage.getItem('ph_phc_abc_posthog')).toBeNull()
    expect(window.localStorage.getItem('unrelated')).toBe('1')
    expect(window.sessionStorage.getItem('ph_session_id')).toBeNull()
    expect(window.sessionStorage.getItem('unrelated')).toBe('1')
  })

  it('leaves a stored refusal alone: a no stays final', () => {
    // The property `start`'s DENIED branch and the `once somebody refuses` describe
    // below both hold: a refusal is not asked again. Clearing it on a withdraw click
    // would put the notice back in front of somebody who already said no.
    run()
    press('No')
    expect(window.localStorage.getItem(KEY)).toBe('denied')

    const reload = vi.fn()
    stubLocation({ reload })
    clickWithdraw()

    expect(window.localStorage.getItem(KEY)).toBe('denied')
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('withdraws an old yes too', () => {
    window.localStorage.setItem(LEGACY_KEY, 'granted')
    const reload = vi.fn()
    stubLocation({ reload })
    clickWithdraw()
    expect(window.localStorage.getItem(KEY)).toBeNull()
    expect(window.localStorage.getItem(LEGACY_KEY)).toBeNull()
    expect(reload).toHaveBeenCalledTimes(1)
  })
})

describe('where the trackers stay silent even after a yes', () => {
  it('is a developer machine or a test runner, and nowhere real', () => {
    // The e2e suite runs Playwright against `vite preview` on localhost and clicks
    // «Va bene» on every measured page: without this rule every CI run would count as
    // visitors in the one project production writes to. The same list as
    // `shared/analytics/posthog.ts`.
    const { measured } = run()
    for (const host of ['localhost', '127.0.0.1', '[::1]', '0.0.0.0', '']) {
      expect(measured(host), host).toBe(false)
    }
    for (const host of ['letsrebase.com', 'www.letsrebase.com', 'preview.letsrebase.com']) {
      expect(measured(host), host).toBe(true)
    }
  })

  it('loads neither SDK on localhost', () => {
    stubLocation({ hostname: 'localhost' })
    run()
    press('Va bene')
    expect(window.localStorage.getItem(KEY)).toBe('granted')
    expect(sdkScripts()).toHaveLength(0)
    expect(posthogScripts()).toHaveLength(0)
    expect(stubs().oaiq).toBeUndefined()
    expect(stubs().posthog).toBeUndefined()
  })

  it('loads PostHog but not the pixel on a preview stack', () => {
    stubLocation({ hostname: 'preview.letsrebase.com' })
    run()
    press('Va bene')
    expect(sdkScripts()).toHaveLength(0)
    expect(stubs().oaiq).toBeUndefined()
    expect(posthogScripts()).toHaveLength(1)
  })
})

describe('the reveal gate', () => {
  it('marks the page as scripted at once, and reveals everything after three seconds', () => {
    vi.useFakeTimers()
    try {
      document.documentElement.className = ''
      new Function(revealGateJs)()
      expect(document.documentElement.classList.contains('js')).toBe(true)
      expect(document.documentElement.classList.contains('reveal-all')).toBe(false)
      vi.advanceTimersByTime(2999)
      expect(document.documentElement.classList.contains('reveal-all')).toBe(false)
      vi.advanceTimersByTime(1)
      expect(document.documentElement.classList.contains('reveal-all')).toBe(true)
    } finally {
      vi.useRealTimers()
      document.documentElement.className = ''
    }
  })
})

describe('once somebody refuses', () => {
  it('loads nothing, and there is no oaiq and no posthog for a signup to call', () => {
    run()
    press('No')

    expect(sdkScripts()).toHaveLength(0)
    expect(posthogScripts()).toHaveLength(0)
    expect(stubs().oaiq).toBeUndefined()
    expect(stubs().posthog).toBeUndefined()
    expect(window.localStorage.getItem(KEY)).toBe('denied')
    expect(notice()).toBeNull()
  })

  it('does not come back, on this page or the next', () => {
    // A notice that reappears until it gets the answer it wants is a dark pattern with
    // a delay.
    run()
    press('No')
    document.body.innerHTML = ''
    run()
    expect(notice()).toBeNull()
    expect(sdkScripts()).toHaveLength(0)
    expect(posthogScripts()).toHaveLength(0)
  })
})

describe('a decision stored before the rename (REB-668)', () => {
  // The key was `orbiters.consent` until REB-668. Somebody who answered then must not be
  // asked again, and a refusal above all stays final: the notice coming back for a
  // visitor who said no is the dark pattern the DENIED branch exists to rule out.
  it('keeps an old yes: no notice, the SDK loads, the decision moves to the new key', () => {
    window.localStorage.setItem(LEGACY_KEY, 'granted')
    run()
    expect(notice()).toBeNull()
    expect(sdkScripts()).toHaveLength(1)
    expect(window.localStorage.getItem(KEY)).toBe('granted')
    expect(window.localStorage.getItem(LEGACY_KEY)).toBeNull()
  })

  it('keeps an old no: no notice, nothing loads, the decision moves to the new key', () => {
    window.localStorage.setItem(LEGACY_KEY, 'denied')
    run()
    expect(notice()).toBeNull()
    expect(sdkScripts()).toHaveLength(0)
    expect(posthogScripts()).toHaveLength(0)
    expect(window.localStorage.getItem(KEY)).toBe('denied')
    expect(window.localStorage.getItem(LEGACY_KEY)).toBeNull()
  })

  it('prefers the new key when both are there', () => {
    window.localStorage.setItem(KEY, 'denied')
    window.localStorage.setItem(LEGACY_KEY, 'granted')
    run()
    expect(sdkScripts()).toHaveLength(0)
    expect(notice()).toBeNull()
  })

  it('writes the new key and drops the old one when a decision is taken', () => {
    const consent = run()
    window.localStorage.setItem(LEGACY_KEY, 'denied')
    consent.decide('granted')
    expect(window.localStorage.getItem(KEY)).toBe('granted')
    expect(window.localStorage.getItem(LEGACY_KEY)).toBeNull()
  })
})

describe('while the notice is up, the page has room under it (ORB-18)', () => {
  // The notice is fixed over the bottom of the viewport. On a phone a short page fits
  // in one screen, so whatever the notice covered stayed covered until the visitor
  // answered: the box's bottom edge at 390 wide, the last line of the note at 360. The
  // script measures the notice and hands the page the same room; system.css spends it.
  const ROOM = '--consent-room'
  const room = () => document.documentElement.style.getPropertyValue(ROOM)

  /** Lays the notice out by hand: jsdom does no layout, so every box measures zero. */
  function layout(box: HTMLElement, top: number, height: number): void {
    Object.defineProperty(box, 'offsetHeight', { configurable: true, value: height })
    box.getBoundingClientRect = () => ({ top, height, bottom: top + height }) as DOMRect
  }

  it('writes the room on the root element as soon as the notice is shown', () => {
    run()
    // Measured, so in a DOM without layout it is zero rather than a guess; the point
    // here is that the property exists from the first paint, not on some later event.
    expect(room()).toBe('0px')
  })

  it('is the distance from the notice top to the bottom of the viewport, whole pixels', () => {
    run()
    // 768 tall (jsdom's default), the notice 81px high with its top at 678.6, so the
    // 8px it keeps from the edge are in the room too: 768 - 678.6 = 89.4, rounded up.
    layout(notice() as HTMLElement, 678.6, 81)
    window.dispatchEvent(new Event('resize'))
    expect(room()).toBe('90px')
    // The sentence wraps to a third line on a narrower phone: a taller notice, more room.
    layout(notice() as HTMLElement, 660.6, 99)
    window.dispatchEvent(new Event('resize'))
    expect(room()).toBe('108px')
  })

  it('takes the room away with the notice, on a yes and on a no', () => {
    run()
    layout(notice() as HTMLElement, 678.6, 81)
    window.dispatchEvent(new Event('resize'))
    press('Va bene')
    expect(room()).toBe('')
    // And the listener went with it: a later resize must not resurrect the room.
    window.dispatchEvent(new Event('resize'))
    expect(room()).toBe('')

    document.body.innerHTML = ''
    document.head.innerHTML = ''
    window.localStorage.clear()
    forget()
    run()
    press('No')
    expect(room()).toBe('')
  })

  it('stops making room for a notice something else took out of the page', () => {
    run()
    layout(notice() as HTMLElement, 678.6, 81)
    window.dispatchEvent(new Event('resize'))
    expect(room()).toBe('90px')
    ;(notice() as HTMLElement).remove()
    window.dispatchEvent(new Event('resize'))
    expect(room()).toBe('')
  })

  it('makes no room when there is no notice to make room for', () => {
    window.localStorage.setItem(KEY, 'denied')
    run()
    expect(notice()).toBeNull()
    expect(room()).toBe('')
  })

  it('is spent by the shared stylesheet at the end of the body, and only there', () => {
    // The other half of the mechanism. A spacer, not body padding: on a page whose
    // body is a grid with a box centred in its first row, a spacer in the last
    const css = readFileSync(join(__dirname, 'system.css'), 'utf-8')
    expect(css).toMatch(/body::after\s*\{[^}]*height:\s*var\(--consent-room, 0px\)/)
    expect(js).toMatch(/var ROOM = '--consent-room'/)
  })
})

describe('when the browser refuses to remember anything', () => {
  it('still shows the notice and still honours the click', () => {
    // Safari in private mode, and any browser set to block site data, throw on
    // `localStorage` rather than returning null. A page that breaks there is worse than
    // one that asks again.
    const storage = window.localStorage
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('site data bloccati')
      },
    })
    try {
      run()
      expect(notice()).toBeTruthy()
      press('Va bene')
      expect(sdkScripts()).toHaveLength(1)
    } finally {
      Object.defineProperty(window, 'localStorage', { configurable: true, value: storage })
    }
  })
})

describe('the notice clears the 44px touch-target floor without shouting (ORB-87)', () => {
  // The behavioural tests above build the notice in jsdom, which lays out no CSS at
  // all: a real render is what actually proves a pixel size, and the PR this test
  // ships with carries one (uishot at 390 and 1440, before and after). What a unit
  // test can hold is the rule shape, so a later edit that quietly drops the floor or
  // swaps it for a heavier control fails here before it fails on a phone.
  const css = readFileSync(join(__dirname, 'system.css'), 'utf-8')

  function rule(selector: string): string {
    const escaped = selector.replace(/[.[\]*+?^${}()|\\]/g, '\\$&')
    const body = css.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`))?.[1]
    expect(body, `rule "${selector}" not found`).toBeTruthy()
    return body ?? ''
  }

  it('gives "No" and "Va bene" a floor on both axes, not just a taller box', () => {
    // Height alone would still leave the two-letter "No" narrower than 44px: the
    // floor is on `min-width` too.
    const button = rule('.consent button')
    expect(button).toMatch(/min-width:\s*2\.75rem/)
    expect(button).toMatch(/min-height:\s*2\.75rem/)
    // Padding and a floor, the ORB-64 shape, not the louder one: the border stays
    // the notice's existing 1px and the resting background stays transparent, no
    // saturated fill added to make the bigger box easier to see.
    expect(button).toMatch(/border:\s*1px solid/)
    expect(button).toMatch(/background-color:\s*transparent/)
  })

  it('grows the "Dettagli" link\'s hit area without growing its line (ORB-87)', () => {
    // The link sits mid-sentence, so a real `min-height` would force the paragraph's
    // lines apart. `position: relative` is what lets the phantom pseudo-element
    // below anchor to the glyph instead of the page.
    expect(rule('.consent a')).toMatch(/position:\s*relative/)
    expect(rule('.consent a')).not.toMatch(/min-height|padding|border/)

    const before = rule('.consent a::before')
    expect(before).toMatch(/content:\s*['"]['"]/)
    expect(before).toMatch(/position:\s*absolute/)
    // No colour and no size property at all: the phantom box paints nothing, so it
    // cannot make the notice louder by construction, only wider to a thumb.
    expect(before).not.toMatch(/background|border|width:|height:/)
    // -14px top/bottom against the glyph's ~16-20px line box clears 44px; the
    // exact number is measured on a render, this only pins the shape surviving.
    expect(before).toMatch(/inset:\s*-14px -4px/)
  })

  it('paints the buttons after the link\'s hit-slop, so a corner click cannot land on the wrong control', () => {
    // `.consent a::before` is positioned, so without this it paints in tree order
    // after the row of buttons and can win a hit test in the sliver where the slop
    // reaches over this row (measured: a real overlap and click-theft toward
    // "Dettagli" at some notice widths before this rule, none after). z-index stays
    // auto -- this only changes paint order among same-level positioned boxes, not
    // anything a reader sees.
    expect(rule('.consent-actions')).toMatch(/position:\s*relative/)
  })

  it('keeps every consent control a real `a`/`button`, so the shared focus-visible rule already covers it', () => {
    // landing.css carries one `::where(a, button…):focus-visible`
    // rule with no scope narrower than the whole page (landing-style.test.ts holds
    // that). This notice earns a visible focus ring for free
    // as long as consent.js keeps building real anchors and buttons rather than a
    // `div` with a click handler -- checked here on the DOM the same file already
    // builds, not by grepping the source for the word `createElement`.
    run().start()
    const box = notice()
    expect(box?.querySelector('a')).toBeInstanceOf(window.HTMLAnchorElement)
    for (const label of ['No', 'Va bene']) {
      const control = [...(box?.querySelectorAll('button') ?? [])].find(
        (candidate) => candidate.textContent === label,
      )
      expect(control, `nessun bottone "${label}"`).toBeInstanceOf(window.HTMLButtonElement)
    }
  })
})
