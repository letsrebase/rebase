import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { SITE_HOST } from './path-map-plugin'

const PAGES = ['index.html', 'pigrocrm.html', 'privacy.html', 'terms.html'] as const
const html = Object.fromEntries(
  PAGES.map((name) => [name, readFileSync(join(__dirname, name), 'utf-8')]),
) as Record<(typeof PAGES)[number], string>

function meta(page: string, name: string): string | undefined {
  return page.match(new RegExp(`<meta\\s+(?:name|property)="${name}"\\s+content="([^"]*)"`))?.[1]
}

describe.each(PAGES)('%s', (name) => {
  const page = html[name]

  it('is in Italian and says so', () => {
    expect(page).toMatch(/<html lang="it">/)
  })

  it('carries its own title, description and Open Graph', () => {
    const title = page.match(/<title>([^<]+)<\/title>/)?.[1] ?? ''
    // Until 2026-09-10 every page here titled itself PigroCRM, including the two
    // legal pages. ORB-36: privacy.html and terms.html are served on
    // letsrebase.com, not on pigro.letsrebase.com, and it is the Orbiters signup
    // form that links to them, so they title themselves after the site they are on
    // rather than after the CRM. index.html is the landing (at / since ORB-145;
    // community.html is the community page at /community) and still names the CRM in
    // its title, the largest of the perks, regardless of Ivan's separate «freelance»
    // (ORB-24, positioning.md line 85); see the brand-link assertion below for the
    // same title/brand split.
    expect(title).toContain(name === 'index.html' ? 'PigroCRM' : 'rebase')
    const description = meta(page, 'description') ?? ''
    expect(description.length).toBeGreaterThan(40)
    // The trap named in spec 9.2: the previous system's index.html still carries "Studio Rossi is
    // the AI optimization platform for human and AI agents" from the scaffold it
    // was generated out of (.reference-*/website/index.html:7-10), describing
    // a product that exists nowhere in that codebase. It is the easiest mistake
    // to repeat, and it is text Google reads during verification.
    expect(description).not.toMatch(/AI optimization platform/i)
    // All three pages' og:title already begins "Orbiters" (index.html's own title
    // keeps the "Con PigroCRM gratis" suffix, but its og:title does not repeat it),
    // so this one assertion covers every page in PAGES with no ternary.
    expect(meta(page, 'og:title')).toContain('rebase')
    expect(meta(page, 'og:description')).toBeTruthy()
    expect(meta(page, 'og:type')).toBe('website')
  })

  it('requests nothing from another origin, bar the one script it declares', () => {
    // REB-111: `<link rel="canonical">` carries this page's own absolute address, on
    // this origin. It is metadata a crawler reads, never a request anywhere, and is
    // checked on its own two lines below rather than against the allowlist here; only
    // that exact value is exempt, not every same-origin absolute URL.
    const canonical = page.match(/<link\b[^>]*\brel="canonical"[^>]*\bhref="([^"]*)"/)?.[1]
    for (const match of page.matchAll(/(?:href|src)="(https?:\/\/[^"]+)"/g)) {
      const url = match[1]!
      if (url === canonical) continue
      // An href the reader clicks -- the repository, the hosted signup, or OpenAI's
      // own privacy policy, which the cookie section has to point at -- is fine; a
      // subresource is not. `humancraft.tech` is in the list because Italian law
      // requires the privacy and terms pages to name the titolare del trattamento and
      // link to it, so those two pages carry a real company's site and this test has to
      // allow the origin; since ORB-116 index.html's footer links the same studio,
      // restored to what production served before website-v0.4.0. It is the only real
      // identity left anywhere in this repository.
      // `www.linkedin.com` since ORB-151: the four voices' avatars link to their public
      // profiles. An href, never a src: the photos themselves are served from here.
      // `posthog.com` since ORB-183: the cookie section links PostHog's policy the way
      // it links OpenAI's. The SDK itself is on `i.posthog.com`, which is not here and
      // never will be: `pixel.test.ts` keeps it out of every page.
      expect(url, 'external subresource').toMatch(
        /^https:\/\/(?:github\.com|pigro\.letsrebase\.com|openai\.com|posthog\.com|humancraft\.tech|www\.linkedin\.com)\//,
      )
    }
    expect(page).not.toMatch(/fonts\.googleapis\.com|fonts\.gstatic\.com/)
    // REB-111: the one `<link>` allowed an absolute href is its own canonical, and only
    // when it points back at this origin; a stylesheet or a preconnect fetched from
    // anywhere else is still banned, which is what this assertion used to say outright.
    for (const tag of page.match(/<link\b[^>]*>/g) ?? []) {
      const href = tag.match(/\bhref="(https?:\/\/[^"]+)"/)?.[1]
      if (href === undefined) continue
      expect(tag, 'the only <link> allowed an absolute href').toMatch(/\brel="canonical"/)
      expect(href.startsWith(`${SITE_HOST}/`), `canonical link ${href} is not on this origin`).toBe(true)
    }
    // Still no third-party tag written into the markup. Since 2026-09-09 index.html
    // *does* fetch one script from another origin -- the ChatGPT Ads measurement SDK,
    // injected by the inline snippet in its head -- and that is the single exception,
    // owned by `pixel.test.ts`: which pages may carry it, which must not, and that no
    // second analytics stack arrives beside it. Leaving this assertion as an
    // unqualified "nothing from another origin" would have made it a sentence that
    // passes while being false, which is worse than no assertion.
    expect(page).not.toMatch(/<script[^>]+src="https?:/)
  })

  it('offers a way into an existing installation', () => {
    // REB-317: whoever opens the root of their own instance must not be stranded
    // on advertising copy. Until 2026-09-21 this sent everyone to PigroCRM's own
    // login (`/app/`), a leftover from before the hub had one of its own; the hub
    // is the front door now, and a member reaches PigroCRM from inside it
    // (`Area.tsx`'s "Apri PigroCRM") or with its own URL directly.
    expect(page).toMatch(/href="\/hub\/accedi"/)
    expect(page).toContain('Accedi')
  })

  it('signs itself with the four-tile glyph before the name', () => {
    // Until 2026-09-10 the landing signed as Orbiters and the two legal pages kept
    // PigroCRM, on the reasoning that a legal page belongs to the product it
    // covers. ORB-36 reopened that: privacy.html and terms.html are served on
    // letsrebase.com, not on pigro.letsrebase.com, the Orbiters signup form is
    // what links to them, and their own text already covers Orbiters' data (the
    // signup) alongside PigroCRM's (community.test.ts separately asserts
    // privacy.html names Orbiters and links /community). All three pages here sign
    // as Orbiters now; the titolare del trattamento the two legal pages name, and
    // the substance of what each policy says, did not move with the brand.
    expect(page).toMatch(
      /<a class="brand" href="\/"><span class="glyph" aria-hidden="true"><\/span>rebase<\/a>/,
    )
  })

  it('carries no reveal library and no grain: what moves is the page\'s own', () => {
    // Since ORB-145 index.html does rise in, block by block, but with its own
    // `data-reveal` attribute and its own script (landing.js), gated so that a page
    // without the script shows everything; `landing-style.test.ts` holds the gate.
    // What stays out is a third-party reveal script and the old grain overlay.
    expect(page).not.toMatch(/class="[^"]*\brise\b|reveal\.js|class="grain"/)
  })
})

describe('index.html', () => {
  const page = html['index.html']

  it('opens with the community and its claim, then presents the perks as a set, the CRM the largest', () => {
    // Since 2026-09-08 PigroCRM is what a member of Orbiters gets: the page says what
    // Orbiters is first, in its own words, and only then what the perks are. Since
    // REB-68 the CRM is named as one of a set, not the whole answer: its own kicker
    // is just "PigroCRM", never "il perk", and the set names both members before the
    // CRM's own expanded block follows.
    const claim = page.indexOf('ma non da soli.')
    const perks = page.indexOf('>I perk<')
    const crmDetail = page.indexOf('>PigroCRM</p>')
    expect(claim).toBeGreaterThan(0)
    expect(perks).toBeGreaterThan(claim)
    expect(page).toContain('<h3>PigroCRM</h3>')
    expect(page).toContain('<h3>La guida</h3>')
    expect(crmDetail).toBeGreaterThan(perks)
    expect(page).not.toContain('PigroCRM, il perk')
    // The hero lead is the one line the pitch deck's cover uses (Ivan, ORB-150); the
    // roles by name moved to the description and the steps, checked further down.
    expect(page.replace(/\s+/g, ' ')).toContain(
      '<p class="lead measure">La community di chi fa software in proprio in Italia.</p>',
    )
    expect(page).toContain('Gratis per chi è in community.')
  })

  it('has two doors in the hero, one per side of the marketplace, both into the hub', () => {
    // The hub (projects/hub) is where somebody signs up since 2026-09-09: the freelancer
    // wizard and the company wizard. Same origin, different deployable; the paths are
    // relative so the page has one origin in every environment.
    expect(page).toMatch(/<a class="cta" href="\/hub\/freelance">Entra come talento<\/a>/)
    expect(page).toMatch(/<a class="cta secondary" href="\/hub\/aziende">[^<]+<\/a>/)
    // The old door, the email form on `/`, is not what this page sells any more.
    expect(page).not.toMatch(/<a class="cta" href="\/community">/)
    // Whoever is already in finds the CRM through its own page (ORB-165): the landing
    // no longer links the registration form directly.
    expect(page).toMatch(/<a class="cta" href="\/pigrocrm">Scopri PigroCRM<\/a>/)
    expect(page).not.toContain('pigro.letsrebase.com/app/registrati')
    // No invented plan or trial: the one price is "be in the community".
    expect(page).not.toMatch(/Prova gratis|abbonamento|piano (Pro|Business)/i)
  })

  it('explains itself in three steps and says what is inside', () => {
    expect(page).toContain('Come funziona')
    // The deck's agenda since ORB-145: three numbered steps on the dark band, no card.
    expect(page.match(/<span class="step-number" aria-hidden="true">0[1-3]<\/span>/g)).toHaveLength(3)
    expect(page).toContain('Cosa trovi dentro')
  })

  it('has one section for the four voices, real people, no placeholder left', () => {
    // Until 2026-09-11 the four tiles were placeholders marked `data-placeholder`, to
    // be counted here when the real ones arrived. They did (Ivan, ORB-145): four people
    // of Orbiters, a role each, one sentence each about the platform.
    expect(page).toContain('Hanno lavorato con noi')
    const tiles = page.match(/<figure class="testimonial"[^>]*>/g) ?? []
    expect(tiles).toHaveLength(4)
    expect(page).not.toContain('data-placeholder')
    // Two talents and two companies, the role line saying which (ORB-146).
    for (const [name, role] of [
      ['Ivan Sala', 'Fractional CTO · talento'],
      ['Lorenzo Fiore', 'Fractional CTO · azienda'],
      ['Luca Franzesi', 'Head of Software Solution · azienda'],
      ['Andrea Ciceri', 'Lead Infrastructure Engineer · talento'],
    ]) {
      expect(page).toMatch(new RegExp(`<strong>${name}</strong><br /><span class="role">${role}</span>`))
    }
    expect(page).not.toMatch(/Mario Rossi|XYZ|Nome Cognome/)
    expect(page.match(/<blockquote>/g)).toHaveLength(4)
    // ORB-151: each tile is the person's face, served from this site, and a link to the
    // profile. Four faces, four LinkedIn links, and no image fetched from LinkedIn.
    const faces = [...page.matchAll(/<a class="avatar" href="(https:\/\/www\.linkedin\.com\/in\/[^"]+)" aria-label="[^"]+ su LinkedIn"><img src="\.\/voices\/([a-z-]+)\.webp" alt="" width="96" height="96" loading="lazy" \/><\/a>/g)]
    expect(faces.map((m) => m[2])).toEqual(['ivan-sala', 'lorenzo-fiore', 'luca-franzesi', 'andrea-ciceri'])
    expect(page).not.toMatch(/src="https?:\/\/[^"]*linkedin/)
  })

  it('says who it is for, in the words that qualify a reader in fifteen seconds', () => {
    // The words are docs/design/positioning.md's (ORB-24): the roles by name, the
    // fiscal reality, the price.
    for (const word of ['developer', 'ai engineer', 'fractional', 'forfettario', 'self-hosted', 'gratis', 'tariffa']) {
      expect(page.toLowerCase()).toContain(word)
    }
    expect(page).toMatch(/\bCTO\b/)
  })

  it('keeps "freelance" as the fiscal category and in the title, never as the claim', () => {
    // It stays in the sentence about forfettario, where it is the legal status, in the
    // hub's route, which is code, and in the <title> and og:title, which Ivan kept on
    // 2026-09-09 for continuity. It is gone from the claim, the descriptions and the CTAs.
    expect(page.match(/<title>([^<]+)<\/title>/)?.[1]).toContain('freelance')
    expect(meta(page, 'og:title')).toBe('rebase — freelance, ma non da soli')
    const headlines = [
      meta(page, 'description'),
      meta(page, 'og:description'),
      page.match(/<h1[^>]*>([\s\S]*?)<\/h1>/)?.[1]?.replace(/<[^>]+>/g, ' '),
      ...[...page.matchAll(/<a class="cta[^"]*" href="[^"]+">([^<]+)<\/a>/g)].map((m) => m[1]),
    ]
    expect(headlines.length).toBeGreaterThanOrEqual(7)
    for (const headline of headlines) expect(headline?.toLowerCase()).not.toContain('freelance')
    expect(page.toLowerCase()).toContain('freelance in italia, spesso in forfettario')
  })

  it('says where the CV goes before asking for it', () => {
    // The wizard takes a CV; a landing that sends people there owes them one sentence
    // about what happens to it, and the link to the rest.
    expect(page).toMatch(/Il CV resta nel nostro database/)
    expect(page).toMatch(/cancelliamo quando ce lo chiedi/)
  })

  it('links the two pages Google reads during verification', () => {
    expect(page).toMatch(/href="\/privacy"/)
    expect(page).toMatch(/href="\/terms"/)
  })

  it('carries WebSite and Organization structured data, with nothing invented', () => {
    // REB-113: one block, on this page only (links.test.ts checks the other five carry
    // none). The legal entity, its VAT number and its contact address are the ones
    // privacy.html and terms.html already state.
    const scripts = [...page.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)]
    expect(scripts).toHaveLength(1)
    const data = JSON.parse(scripts[0]![1]!)
    expect(data['@context']).toBe('https://schema.org')
    expect(data['@graph']).toHaveLength(2)
    const website = data['@graph'].find((node: { '@type': string }) => node['@type'] === 'WebSite')
    const organization = data['@graph'].find((node: { '@type': string }) => node['@type'] === 'Organization')
    // toMatchObject alone would not fail on an extra, invented property (a postal
    // address, say); the exact key set is checked too, so the "nothing invented" rule
    // this test's name promises actually holds.
    expect(Object.keys(website).sort()).toEqual(['@type', 'name', 'url'].sort())
    expect(website).toMatchObject({ name: 'rebase', url: 'https://letsrebase.com/' })
    expect(Object.keys(organization).sort()).toEqual(
      ['@type', 'name', 'legalName', 'url', 'logo', 'vatID', 'email'].sort(),
    )
    expect(organization).toMatchObject({
      name: 'rebase',
      legalName: 'Humancraft di Ivan Sala',
      url: 'https://letsrebase.com/',
      vatID: '14518240966',
      email: 'ivansala@humancraft.tech',
    })
    // The logo is checked against the page's own og:image rather than a second
    // hardcoded literal, so a future redraw (the numbered file REB-205 already owns)
    // cannot update one and silently leave the other stale.
    expect(organization.logo).toBe(meta(page, 'og:image'))
  })

  it('signs its footer with the studio behind the site, never with a fixture', () => {
    // ORB-116: the pre-publication sanitisation swapped this link for «Studio Rossi» at
    // example.com, the suite's stock customer, and website-v0.4.0 shipped it. The studio
    // is the same entity the two policy pages name as titolare, so the footer says what
    // they say; a fixture host on a public page fails here and in links.test.ts.
    const footer = page.match(/<footer[\s\S]*?<\/footer>/)?.[0] ?? ''
    expect(footer).toMatch(/<a[^>]*\bhref="https:\/\/humancraft\.tech\/"[^>]*>Humancraft<\/a>/)
    expect(page).not.toMatch(/Studio Rossi|example\.com/)
  })

  it('opens a door into the hub admin area from its footer, and only there', () => {
    // Ivan, 2026-09-10 (ORB-105): whoever reviews signups, freelancers and companies
    // should not have to type the admin URL by hand. One quiet link, last in the
    // footer, dressed like its neighbours. It goes to `/hub/admin/freelance`, the first
    // screen of the area and where the hub's own login lands, rather than to `/hub/admin`:
    // the hub router has no index route under `/admin`, so a signed-in admin sent there
    // would see the frame with an empty panel (found in review of PR 28, filed as ORB-106).
    // `AdminLayout` still sends a visitor without a session to its login first.
    const footer = page.match(/<footer[\s\S]*?<\/footer>/)?.[0] ?? ''
    expect(footer).toMatch(/<a[^>]*\bhref="\/hub\/admin\/freelance"[^>]*>Admin<\/a>\s*<\/p>/)
    expect(page.match(/href="\/hub\/admin/g)).toHaveLength(1)
  })

  it('mounts the same field as the community page behind the whole page, from the shared script', () => {
    // Ivan, 2026-09-09: the landing has the same background as the community page. One fixed canvas
    // right after <body>, the same id, the same mount options in landing.js.
    expect(page).toMatch(/<body>\s*(?:<!--[\s\S]*?-->\s*)?<canvas id="field" aria-hidden="true"><\/canvas>/)
    expect(page).not.toContain('hero-field')
    expect(page).toMatch(/<script type="module" src="\.\/field\.js"><\/script>\s*<script type="module" src="\.\/landing\.js">/)
  })

  it('collects nothing, and measures only after the visitor has agreed to it', () => {
    // Nothing is typed on this page: the forms live in the hub. What arrived on
    // 2026-09-09 is the measurement pixel, because this is a page an ad lands on -- so
    // "measures nothing" stopped being true, and pretending otherwise here would have
    // meant a test asserting the absence of a string that is in the file. What the page
    // carries is the consent script, and only that can load the pixel;
    // `pixel.test.ts` and `consent.test.ts` hold the gate itself.
    expect(page).not.toMatch(/<form/i)
    expect(page).not.toMatch(/<input/i)
    expect(page).not.toMatch(/gtag|googletagmanager|plausible|fathom|hotjar/i)
    expect(page).not.toContain('oaiq')
    expect(page.match(/<script[^>]+consent\.js/g)).toHaveLength(1)
  })
})

describe('privacy.html', () => {
  const page = html['privacy.html']

  it('names both restricted Gmail scopes, in full', () => {
    // Spec 13, criterion 26. Not pedantry: Google's review of a restricted scope
    // checks that the privacy policy states what the application does with the
    // data. Without this text 5B-1 stays in Testing, where a consumer refresh
    // token expires every seven days.
    expect(page).toContain('https://www.googleapis.com/auth/gmail.readonly')
    expect(page).toContain('https://www.googleapis.com/auth/gmail.send')
  })

  it('says what is read, what is stored, and what is never touched', () => {
    for (const claim of [
      'indirizzi email già presenti',
      'non leggiamo',
      'non trasferiamo',
      'sul tuo server',
      'revocare',
    ]) {
      expect(page.toLowerCase()).toContain(claim.toLowerCase())
    }
  })

  it('names the scopes it deliberately does not ask for', () => {
    // The consent screen shows what is requested; the policy is where "and not
    // these" belongs. gmail.modify would let the product touch the mailbox, and
    // it never does: the state lives in the CRM.
    expect(page).toContain('gmail.modify')
    expect(page).toContain('https://mail.google.com/')
  })

  it('gives a date, so a reviewer can tell when it was last true', () => {
    expect(page).toMatch(/<time datetime="\d{4}-\d{2}-\d{2}">/)
  })
})

describe('terms.html', () => {
  const page = html['terms.html']

  it('is honest that there is no service being provided', () => {
    for (const claim of ['nessuna garanzia', 'software', 'licenza']) {
      expect(page.toLowerCase()).toContain(claim)
    }
    expect(page).not.toMatch(/abbonamento|canone|SLA/i)
  })
})
