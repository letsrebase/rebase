/* The two trackers, the ChatGPT Ads pixel and PostHog: what may load them, where, and
 * the strings that must never appear in a page.
 *
 * Every rule about a tracker lives in this file rather than being spread over the page
 * tests, because they are rules about one decision -- we measure the ad conversion and
 * how the site is used, we measure nothing else, and we load nothing before somebody
 * says yes -- and a rule split across three files is a rule that gets half-changed. The
 * page tests keep saying what a page is; this says what it may fetch.
 *
 * The consent mechanics themselves are `consent.test.ts`, which drives the script in a
 * DOM. What is here is the static half: which pages can ever load a tracker, which must
 * not, that no page contains a key, and that the PostHog literals in consent.js are the
 * ones every other surface reads from `shared/analytics`.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { POSTHOG_ASSET_HOST, POSTHOG_HOST, POSTHOG_KEY } from '@rebase/analytics'
import { describe, expect, it } from 'vitest'

const PIXEL_ID = '9r6qrnPxBV8WDVGtpuaqxh'
const SDK_URL = 'https://bzrcdn.openai.com/sdk/oaiq.min.js'
const POSTHOG_URL = `${POSTHOG_ASSET_HOST}/static/array.js`

const MEASURED = ['index.html', 'pigrocrm.html'] as const
const UNMEASURED = ['privacy.html', 'terms.html'] as const
const ALL = [...MEASURED, ...UNMEASURED]

// Keyed by the literal names rather than by `string`: `noUncheckedIndexedAccess` makes
// a string index return `string | undefined`, and the assertions below read the page
// directly. Same shape as `landing-pages.test.ts`.
const page = Object.fromEntries(
  ALL.map((name) => [name, readFileSync(join(__dirname, name), 'utf-8')]),
) as Record<(typeof ALL)[number], string>
const consent = readFileSync(join(__dirname, 'consent.js'), 'utf-8')

describe.each(MEASURED)('%s', (name) => {
  it('loads the consent script, and that is the only way the pixel can arrive', () => {
    expect(page[name]).toMatch(/<script type="module" src="\.\/consent\.js"><\/script>/)
    // One script element, so one notice and one init. Counted as tags rather than as
    // occurrences of the name, which the comment above it also contains.
    expect(page[name].match(/<script[^>]+consent\.js/g)).toHaveLength(1)
  })

  it('contains no tracker of its own: no id, no key, no SDK, no init', () => {
    // The gate is worth nothing if a snippet is also sitting in the markup. Before
    // 2026-09-09 the pixel was, and consent was the change that took it out: markup
    // runs whatever the visitor decided. PostHog arrived on 2026-09-12 through the
    // same door and never through the head.
    expect(page[name]).not.toContain(PIXEL_ID)
    expect(page[name]).not.toContain(SDK_URL)
    expect(page[name]).not.toContain('oaiq')
    expect(page[name]).not.toContain(POSTHOG_KEY)
    expect(page[name]).not.toMatch(/https:\/\/[a-z-]+\.i\.posthog\.com/)
    for (const [block] of page[name].matchAll(/<script\b[^>]*>[\s\S]*?<\/script>/g)) {
      expect(block).not.toMatch(/posthog/i)
    }
  })
})

describe.each(UNMEASURED)('%s', (name) => {
  it('carries no pixel and no notice, because it has nothing to ask about', () => {
    // A tracker on the privacy policy is the one place it cannot be defended, and
    // neither page is a landing an ad can point at -- so neither needs a notice either.
    expect(page[name]).not.toContain('consent.js')
    expect(page[name]).not.toContain('oaiq')
    expect(page[name]).not.toContain('bzrcdn')
    // A *link* to PostHog's policy is not a tracker either -- privacy.html points at it,
    // and names the EU host in prose; what is refused is a URL to it, and a key.
    expect(page[name]).not.toMatch(/https:\/\/[a-z-]+\.i\.posthog\.com/)
    expect(page[name]).not.toContain(POSTHOG_KEY)
    for (const [block] of page[name].matchAll(/<script\b[^>]*>[\s\S]*?<\/script>/g)) {
      expect(block).not.toMatch(/posthog/i)
    }
    // A *link* to OpenAI's policy is not a pixel -- privacy.html has to point at it --
    // so what is refused is a script, not the name.
    expect(page[name]).not.toMatch(/<script[^>]*>[\s\S]*openai/i)
  })
})

describe.each(ALL)('%s', (name) => {
  it('contains no API key', () => {
    // The Conversions API key is a secret with full read and write access to the
    // conversion source. It belongs to the server's .env and to nothing that is served
    // to a browser -- and the cheapest way for it to end up in a page is somebody
    // pasting the whole setup snippet from the OpenAI console.
    expect(page[name]).not.toMatch(/sk-[A-Za-z0-9_-]{8}/)
    expect(page[name]).not.toMatch(/Authorization|Bearer/i)
  })

  it('measures through consent.js or not at all', () => {
    // No third analytics stack arriving next to the two that enter through the gate.
    // Deliberately a closed list rather than a free hand.
    expect(page[name]).not.toMatch(
      /gtag|googletagmanager|plausible|fathom|hotjar|matomo|segment\.com/i,
    )
  })
})

describe('consent.js', () => {
  it('is the only file that names the pixel id, the SDK, or the PostHog project', () => {
    expect(consent).toContain(PIXEL_ID)
    expect(consent).toContain(SDK_URL)
    const external = [...consent.matchAll(/https:\/\/[a-z0-9.-]+\.[a-z]+[^'"\s]*/g)]
    expect(external.map((match) => match[0]).sort()).toEqual(
      [SDK_URL, POSTHOG_HOST, POSTHOG_ASSET_HOST].sort(),
    )
    // Not duplicated anywhere else in the codebase: one id, one place.
  })

  it('privacy.html withdraws the same key it names, held in lockstep here', () => {
    // privacy.html carries no reference to this script (the test above), so it
    // reimplements decide's two steps against the same key by hand rather than
    // calling window.__consent.withdraw; a rename here has to fail loudly there.
    const key = consent.match(/var STORAGE_KEY = '([^']+)'/)?.[1]
    expect(key).toBeTruthy()
    expect(page['privacy.html']).toContain(`getItem('${key}') === 'granted'`)
    expect(page['privacy.html']).toContain(`removeItem('${key}')`)
  })

  it('carries the same PostHog project as every other surface', () => {
    // `shared/analytics/posthog.ts` is the source; this file cannot import it because
    // it runs without a bundler, so it repeats the three values and this test is what
    // keeps the copy honest -- the same idiom path-map-plugin.test.ts uses for
    // nginx.conf.
    expect(consent).toContain(`var POSTHOG_KEY = '${POSTHOG_KEY}'`)
    expect(consent).toContain(`var POSTHOG_HOST = '${POSTHOG_HOST}'`)
    expect(consent).toContain(`var POSTHOG_ASSET_HOST = '${POSTHOG_ASSET_HOST}'`)
    // The literal in the DOM test above is the URL the browser will actually request.
    expect(readFileSync(join(__dirname, 'consent.test.ts'), 'utf-8')).toContain(POSTHOG_URL)
  })

  it('injects both SDKs from inside the accepting branch and nowhere else', () => {
    // The assertion that keeps the gate a gate. `loadPixel` and `loadPostHog` are the
    // only functions that touch a script's `src`, both are called only from `accept`,
    // and the only call to `accept` that is not behind a stored `granted` is in
    // `decide`, after the click.
    const pixel = consent.slice(
      consent.indexOf('function loadPixel'),
      consent.indexOf('function loadPostHog'),
    )
    const posthog = consent.slice(
      consent.indexOf('function loadPostHog'),
      consent.indexOf('function button'),
    )
    expect(pixel).toContain('script.src = SDK_URL')
    expect(posthog).toContain("script.src = POSTHOG_ASSET_HOST + '/static/array.js'")
    // A `src` is assigned in exactly two places in the file, and both are above.
    expect(consent.match(/\.src = /g)).toHaveLength(2)
    const accept = consent.slice(consent.indexOf('function accept'), consent.indexOf('function decide'))
    expect(accept).toContain('loadPixel()')
    expect(accept).toContain('loadPostHog()')
    // Calls, not definitions: `function loadPixel()` is the one place each is written
    // besides its call.
    expect(consent.match(/(?<!function )loadPixel\(\)/g)).toHaveLength(1)
    expect(consent.match(/(?<!function )loadPostHog\(\)/g)).toHaveLength(1)
    expect(consent).toContain('if (decision === GRANTED) return accept()')
    expect(consent).toContain('if (decision === GRANTED) accept()')
    expect(consent.match(/(?<!function )accept\(\)/g)).toHaveLength(2)
  })

  it('asks again only while nothing has been decided', () => {
    // A refusal is remembered, so the notice does not come back on every page. That is
    // the difference between a notice and a nuisance.
    expect(consent).toContain('if (decision === DENIED) return')
    expect(consent).toContain('localStorage')
  })
})

describe('privacy.html', () => {
  it('says the pixel exists, what it sends, and what it does not', () => {
    // The page used to read "nessun cookie di terze parti, nessuna analitica, nessun
    // pixel", which stopped being true the moment the snippet shipped. A privacy policy
    // that describes a site other than the one being served is worse than no policy, so
    // this test exists to make the two change together.
    const policy = page['privacy.html']
    expect(policy).toContain('ChatGPT Ads')
    expect(policy).toContain('__obref')
    // The three things we deliberately do not hand over. `emails_sha256` is off by
    // default in the API (`openai_conversions_send_hashed_email`), and if it is ever
    // switched on, this sentence becomes false and has to be rewritten first.
    expect(policy).toContain('Non gli mandiamo il tuo nome, la tua email né il tuo profilo')
    expect(policy).toContain('openai.com/policies/privacy-policy')
    // And the old absolute claim is gone rather than merely contradicted further down.
    expect(policy).not.toMatch(/Nessun cookie di terze parti, nessuna analitica, nessun\s+pixel/)
  })

  it('says the pixel waits for a yes, and how to change your mind', () => {
    // Since the notice exists, the policy has to describe the actual mechanism rather
    // than an interest we assert: consent first, and a way back.
    const policy = page['privacy.html']
    expect(policy).toMatch(/consenso/i)
    // Both halves of the date: the attribute a machine reads and the words a person does.
    // The first version of this change moved one and not the other.
    expect(policy).toContain('<time datetime="2026-09-26">26 settembre 2026</time>')
  })

  it('says PostHog is there, on the site after the yes and behind the logins without one', () => {
    // The page used to promise «nessuna analitica» inside the CRM. That stops being
    // true the day the CRM's card (ORB-184) ships, so the sentence changes first, here,
    // and this test keeps the policy and the code moving together.
    const policy = page['privacy.html']
    expect(policy).toContain('PostHog')
    expect(policy).toMatch(/eu\.i\.posthog\.com|in Europa/)
    expect(policy).toContain('posthog.com/privacy')
    expect(policy).not.toMatch(/nessuna analitica e nessun\s+tracciamento/)
    // What is sent about a signed-in person, named, and the legal basis.
    expect(policy).toMatch(/interesse legittimo/i)
    for (const claim of ['email', 'nome', 'ruolo', 'spazio']) expect(policy).toContain(claim)
    // The site records sessions too (consent.js sets `session_recording`), and the hub
    // masks its inputs but not every text: the policy says exactly that, no more.
    expect(policy).toContain('registra la sessione con i campi mascherati')
    expect(policy).toContain('ogni campo è mascherato, e nel CRM anche ogni testo')
    expect(policy).toContain('indirizzo IP e il tuo browser')
  })
})
