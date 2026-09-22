/**
 * What a link to this site shares with (ORB-112).
 *
 * Every page of the site, not the four `landing-pages.test.ts` covers: a link to the
 * pitch is shared as readily as a link to the front door, and
 * the head that forgets these tags is the one nobody looks at. Until this existed the
 * pages named a title and a description and no image, so a client fell back to the
 * first large picture in the markup, which on the landing is one of the four faces in
 * the voices section, and Ivan's own face is the first of them.
 */
import { readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { BRAND_TILES } from '@rebase/brand/mark'
import { describe, expect, it } from 'vitest'
import { SITE_HOST } from './path-map-plugin'

const PAGES = [
  'index.html',
  'pigrocrm.html',
  'pitch.html',
  'privacy.html',
  'terms.html',
] as const

/** The name carries a number and a redraw takes the next one: LinkedIn, Facebook and
 *  WhatsApp cache a card per URL for days, so a card redrawn under its old name goes on
 *  sharing as the old card. This constant, the generator's and the six heads move
 *  together. */
const CARD_FILE = 'share-card-2.png'
const CARD_PATH = `/assets/${CARD_FILE}`
const CARD_URL = `${SITE_HOST}${CARD_PATH}`
const CARD_ON_DISK = join(__dirname, 'public', 'assets', CARD_FILE)
const CARD_ALT =
  'Il marchio rebase, quattro tessere su fondo blu, con il claim «freelance, ma non da soli»'
const WIDTH = 1200
const HEIGHT = 630

const html = Object.fromEntries(
  PAGES.map((name) => [name, readFileSync(join(__dirname, name), 'utf-8')]),
) as Record<(typeof PAGES)[number], string>

const generator = readFileSync(join(__dirname, '..', 'scripts', 'share-card.js'), 'utf-8')
const nginx = readFileSync(join(__dirname, '..', 'deploy', 'nginx.conf'), 'utf-8')

function meta(page: string, name: string): string | undefined {
  // `[^>]*`, never `[\s\S]*?`: the alt is long enough that prettier wraps its tag over
  // three lines, so the pattern has to cross newlines, but one that can also cross a
  // `>` reads the *next* tag's content for a tag that has none of its own, and asserts
  // a value belonging to something it did not name.
  return page.match(new RegExp(`<meta[^>]*(?:name|property)="${name}"[^>]*content="([^"]*)"`))?.[1]
}

describe.each(PAGES)('%s shares with the card', (name) => {
  const page = html[name]

  it('names the site, the image and the card type', () => {
    expect(meta(page, 'og:site_name')).toBe('rebase')
    expect(meta(page, 'og:image')).toBe(CARD_URL)
    expect(meta(page, 'og:image:type')).toBe('image/png')
    expect(meta(page, 'og:image:width')).toBe(String(WIDTH))
    expect(meta(page, 'og:image:height')).toBe(String(HEIGHT))
    expect(meta(page, 'twitter:card')).toBe('summary_large_image')
  })

  it('describes the image for a reader who cannot see it', () => {
    // A card with no alt is an image a screen reader announces as a filename, and
    // LinkedIn reads this one out in the preview it builds. X reads its own key rather
    // than the Open Graph one, so both are declared and both say the same thing.
    //
    // A word of warning for whoever edits these heads: `landing-pages.test.ts` refuses
    // the string «SLA» anywhere in terms.html, with no word boundary, so a comment
    // there that names a certain chat application fails a test about subscriptions.
    expect(meta(page, 'og:image:alt')).toBe(CARD_ALT)
    expect(meta(page, 'twitter:image:alt')).toBe(CARD_ALT)
  })

  it('carries a title and a description to go with it', () => {
    // The pitch had neither until now: `noindex` is about crawling, not about sharing.
    expect(meta(page, 'og:title')).toBeTruthy()
    expect((meta(page, 'og:description') ?? '').length).toBeGreaterThan(40)
  })
})

describe('the card itself', () => {
  it('is served from the one path besides the pages that nginx answers', () => {
    // Not `route()` from the path map: that answers `{kind: 'file'}` for any path with
    // a dot in it, so it would pass for a card that exists nowhere. What decides in
    // production is this block, and the card has to sit under it; `public/assets/` is
    // how a file keeps an unhashed name and still lands there. The 200 itself is
    // asserted against a real server in `e2e/site.spec.ts`.
    expect(nginx).toMatch(/location \/assets\/\s*\{\s*try_files \$uri =404;\s*\}/)
    expect(CARD_PATH.startsWith('/assets/')).toBe(true)
    expect(statSync(CARD_ON_DISK).isFile()).toBe(true)
  })

  it('is a PNG of exactly the size the tags promise', () => {
    const png = readFileSync(CARD_ON_DISK)
    expect(png.subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))
    // IHDR is the first chunk: width and height are two big-endian 32-bit integers at
    // byte 16. A card of the wrong size is one LinkedIn crops or refuses to enlarge.
    expect(png.readUInt32BE(16)).toBe(WIDTH)
    expect(png.readUInt32BE(20)).toBe(HEIGHT)
  })

  it('is small enough that every client fetches it', () => {
    // WhatsApp gives up above 600 kB and shows no image at all; this one is about 30.
    expect(statSync(CARD_ON_DISK).size).toBeLessThan(600_000)
  })

  it('is the file the generator writes, under the name the heads ask for', () => {
    expect(generator).toContain(`export const CARD_FILE = '${CARD_FILE}'`)
  })

  it('never types the old name back into the generator', () => {
    // REB-205: the card drew `Orbiters` as text long after the rename, because the
    // wordmark it draws was never checked against the copy rule the rest of the site
    // follows. This reads the generator's own source, the same `generator` the hex
    // guard below reads, so it catches the word coming back as a string literal in
    // `share-card.js`; the brand file it now reads at runtime is pinned separately, by
    // `landing-style.test.ts`, which holds every SVG's geometry and fill to the palette.
    expect(generator).not.toContain('Orbiters')
  })

  it('draws the four tiles in the order the shared mark declares', () => {
    // The fourth surface to draw this mark, after the application, the website's own
    // `.glyph` and the favicon, and `shared/brand/mark.ts` exists because three of them
    // drifting apart is silent. On the ink ground the two ink tiles are drawn white,
    // the substitution `pitch.css` already makes on its dark slides: white for ink,
    // and the two tokens for the other two.
    const onInk: Record<(typeof BRAND_TILES)[number], string> = {
      ink: '#ffffff',
      'royal-gold': '${gold}',
      watermelon: '${melon}',
    }
    const shadow = generator.match(/box-shadow:([\s\S]*?);/)?.[1] ?? ''
    const drawn = [...shadow.matchAll(/(#[0-9a-fA-F]{6}|\$\{\w+\})/g)].map((match) => match[1])
    // The first tile is the element's own background, the other three its shadows.
    const background = generator.match(/\.glyph \{[\s\S]*?background-color: (#[0-9a-fA-F]{6});/)?.[1]
    expect([background, ...drawn]).toEqual(BRAND_TILES.map((tile) => onInk[tile]))
  })

  it('is drawn from the shared palette rather than from hexes of its own', () => {
    const hexes = [...generator.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((match) => match[0])
    // White is the one literal the card is allowed: it is not a brand token, it is the
    // paper the ink is not, and `system.css` spells `#ffffff` for the same reason. The
    // grid line is `rgba(255, 255, 255, 0.05)`, the same white through the ground, and
    // is deliberately outside this guard.
    expect(hexes.filter((hex) => hex.toLowerCase() !== '#ffffff')).toEqual([])
    expect(generator).toContain('palette.css')
  })
})
