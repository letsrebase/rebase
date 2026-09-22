import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { BRAND_TILES, BRAND_TILE_VARS } from '@rebase/brand/mark'

const css = readFileSync(join(__dirname, 'landing.css'), 'utf-8')
const system = readFileSync(join(__dirname, 'system.css'), 'utf-8')
const appTokens = readFileSync(fileURLToPath(import.meta.resolve('@rebase/brand/palette.css')), 'utf-8')

/** The declarations of one rule, by exact selector. */
function rule(selector: string, source = css): string {
  const escaped = selector.replace(/[.[\]*+?^${}()|\\]/g, '\\$&')
  const body = source.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`))?.[1]
  if (!body) throw new Error(`rule "${selector}" not found`)
  return body
}

describe('the landing shares the product system', () => {
  it('sits on the grid system.css declares, as Orbiters does', () => {
    expect(rule('body')).toMatch(/linear-gradient\(to right, var\(--landing-grid\) 1px, transparent 1px\)/)
    expect(rule('body')).toMatch(/linear-gradient\(to bottom, var\(--landing-grid\) 1px, transparent 1px\)/)
    expect(rule('body')).toMatch(/background-size:\s*var\(--landing-cell\) var\(--landing-cell\)/)
    // Both sheets point at the shared line and tile rather than restating them.
    expect(css).toMatch(/--landing-grid:\s*var\(--system-grid\)/)
    expect(css).toMatch(/--landing-cell:\s*var\(--system-cell\)/)
    expect(system).toMatch(/--system-grid:\s*color-mix\(in oklab, var\(--color-prussian-blue\) 7%, transparent\)/)
    expect(system).toMatch(/--system-cell:\s*16px/)
    expect(css).not.toMatch(/color-mix\([^)]*7%/)
  })

  it('draws the grid on these two surfaces only: the app stopped', () => {
    // This assertion used to read the other way round -- the app restated the same
    // line and tile, because Tailwind cannot import system.css. The app UI revision
    // (2026-09-08, docs/superpowers/specs/2026-09-08-ui-revision-design.md §3) removed
    // the grid from the app body: its white content panel covers the page, and the
    // grid was the one thing tying the app's shapes to Orbiters'. The landing and
    // Orbiters are unchanged and keep it, so system.css is now its single declaration
    // and there is nothing left in the app to drift from it.
    expect(appTokens).not.toMatch(/--grid-line/)
    expect(appTokens).not.toMatch(/background-image:/)
  })

  it('has hard edges: no radius, no blur, no soft shadow, no grain', () => {
    expect(css).not.toMatch(/border-radius:(?!\s*0;)/)
    expect(css).not.toMatch(/backdrop-filter|blur\(|radial-gradient|repeating-linear-gradient/)
    for (const [, shadow] of css.matchAll(/box-shadow:\s*([^;]+);/g)) {
      // Offsets only: `x y 0 colour`, never a blur radius.
      for (const layer of (shadow ?? '').split(',')) {
        expect(layer.trim()).toMatch(/^-?[\d.]+(?:px|rem)? -?[\d.]+(?:px|rem)? 0 |^var\(--landing-step\) var\(--landing-step\) 0 /)
      }
    }
  })

  it('draws its lines in the ink, two pixels wide, and never in a grey of its own', () => {
    expect(rule('*')).toMatch(/border-color:\s*var\(--landing-ink\)/)
    expect(rule('.box')).toMatch(/border-width:\s*2px/)
    expect(rule('.card')).toMatch(/border-width:\s*2px/)
    expect(rule('.rule')).toMatch(/border-top-width:\s*2px/)
    expect(css).not.toMatch(/hairline|--border\b/)
    expect(system).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('lifts the box off the grid by a whole step the colour of the ink', () => {
    expect(rule('.box')).toMatch(/box-shadow:\s*var\(--landing-step\) var\(--landing-step\) 0 var\(--landing-ink\)/)
    expect(rule('.card')).toMatch(/box-shadow:\s*6px 6px 0 var\(--landing-ink\)/)
  })

  it('signs itself with the four tiles of the brand mark, in reading order', () => {
    expect(css).not.toMatch(/\.glyph/)
    const glyph = rule('.glyph', system)
    // One element and three shadows: the tile itself is the first of the four.
    const drawn = [
      glyph.match(/background-color:\s*([^;]+);/)?.[1]?.trim(),
      glyph.match(/6px 0 0 ([^,\n]+)/)?.[1]?.trim(),
      glyph.match(/0 6px 0 ([^,\n]+)/)?.[1]?.trim(),
      glyph.match(/6px 6px 0 ([^,;\n]+)/)?.[1]?.trim(),
    ]
    // Against `shared/brand`, not against the application's component: the mark is
    // the brand's, and three surfaces draw it in three technologies. The application
    // asserts the same order on its own side, in BrandMark.test.tsx.
    expect(drawn).toEqual(BRAND_TILES.map((tile) => BRAND_TILE_VARS[tile]))
  })

  it('reveals the deck\'s blocks only behind the script gate, and never by scroll-driven CSS (ORB-145)', () => {
    // Until 2026-09-11 this read "no entrance animation": the initial state was the
    // final state, so a failed script could leave nothing hidden. Ivan's ruling that
    // day (docs/design/DECISIONS.md): the landing keeps the pitch deck's rhythm, and
    // its blocks rise in. The promise that survives is the second half -- nothing a
    // failed script can leave hidden: a block is hidden only under `html.js`, which
    // the inline gate in index.html's head sets, `.reveal-all` shows everything after
    // three seconds whatever the script did, and reduced motion turns it all off.
    const hidden = rule('.js .deck [data-reveal]')
    expect(hidden).toMatch(/opacity:\s*0/)
    expect(hidden).toMatch(/transition:/)
    expect(css).toMatch(/\.js\.reveal-all \.deck \[data-reveal\]\s*\{[^}]*opacity:\s*1/)
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.js \.deck \[data-reveal\]\s*\{[^}]*transition:\s*none/)
    // No other transition or animation, and nothing keyed to the scroll position.
    expect(css.match(/transition:/g)).toHaveLength(2)
    expect(css).not.toMatch(/\.rise|data-hidden|@keyframes|animation:/)
    expect(css).not.toMatch(/animation-timeline|scroll\(\)|view\(\)/)
    // The gate is in the page, first thing after the stylesheet, and in no other page.
    const index = readFileSync(join(__dirname, 'index.html'), 'utf-8')
    expect(index).toMatch(/<script>\s*document\.documentElement\.classList\.add\('js'\)/)
    expect(index).toMatch(/classList\.add\('reveal-all'\)\s*\}, 3000\)/)
    for (const name of ['privacy.html', 'terms.html']) {
      expect(readFileSync(join(__dirname, name), 'utf-8')).not.toMatch(/data-reveal|class="deck"/)
    }
  })

  describe('the title\'s cursor (ORB-24)', () => {
    it('is a bar of the ink, blinking in steps, only while the script types', () => {
      const cursor = rule('h1 .role.is-typing::after', system)
      expect(cursor).toMatch(/content:\s*''/)
      expect(cursor).toMatch(/background-color:\s*currentColor/)
      expect(cursor).toMatch(/animation:\s*blink 1s step-end infinite/)
      expect(system).toMatch(/@keyframes blink\s*\{[^}]*50%\s*\{\s*opacity:\s*0;?\s*\}/)
      // No hex, no other colour: the bar is the text's own.
      expect(cursor).not.toMatch(/#[0-9a-fA-F]{3,8}\b|var\(--color/)
    })

    it('is gone under reduced motion, whatever the script did', () => {
      expect(system).toMatch(
        /@media \(prefers-reduced-motion: reduce\)\s*\{\s*h1 \.role::after\s*\{\s*display:\s*none;/,
      )
    })

    it('leaves the sr-only text out of the layout, once', () => {
      expect(rule('.sr-only', system)).toMatch(/position:\s*absolute/)
      expect(rule('.sr-only', system)).toMatch(/clip-path:\s*inset\(50%\)/)
      expect(css).not.toMatch(/\n\.sr-only\s*\{/)
    })

    it('does not let the testimonial\'s role size reach the title', () => {
      // `.role` was the testimonial caption's class before it was the title's word;
      // the small size stays on the caption.
      expect(rule('.who .role')).toMatch(/font-size:\s*1rem/)
      expect(css).not.toMatch(/\n\.role\s*\{/)
    })
  })

  it('tracks out the deck\'s kicker on the landing only; the policy pages keep the sentence with its tile', () => {
    // ORB-145: the landing's kicker is the pitch deck's, capitals in the accent, and it
    // lives under `.deck`, which only index.html's <main> carries. `.kicker` itself is
    // unchanged, so privacy.html and terms.html still open with a sentence and a
    // tile, and a <time> inside it still flows as text.
    expect(rule('.deck .kicker')).toMatch(/text-transform:\s*uppercase/)
    expect(rule('.deck .kicker')).toMatch(/letter-spacing:\s*0\.12em/)
    expect(css.match(/text-transform:\s*uppercase/g)).toHaveLength(1)
    expect(css).not.toMatch(/letter-spacing:\s*0\.[2-9]/)
    expect(rule('.kicker')).not.toMatch(/text-transform/)
    expect(rule('.kicker::before')).toMatch(/background-color:\s*var\(--landing-cta\)/)
    expect(rule('.deck .kicker::before')).toMatch(/display:\s*none/)
  })

  it('turns the field and the grid off when the reader asked for more contrast, once', () => {
    const block = system.match(/@media \(prefers-contrast: more\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
    expect(block).toMatch(/body\s*\{\s*background-image:\s*none/)
    expect(block).toMatch(/canvas\s*\{\s*display:\s*none/)
    expect(css).not.toMatch(/prefers-contrast/)
  })

  it('keeps the kicker inline, so a sentence with a <time> in it still flows', () => {
    expect(rule('.kicker')).not.toMatch(/display:\s*flex/)
    expect(rule('.kicker::before')).toMatch(/display:\s*inline-block/)
  })

  it('paints the field: fixed, behind everything, inert', () => {
    expect(rule('#field')).toMatch(/position:\s*fixed/)
    expect(rule('#field')).toMatch(/inset:\s*0/)
    expect(rule('#field')).toMatch(/z-index:\s*-1/)
    expect(rule('#field')).toMatch(/pointer-events:\s*none/)
    expect(css).not.toMatch(/hero-field|isolation/)
  })

  it('keeps the call to action square, saturated once, and ink on hover', () => {
    expect(rule('.cta')).toMatch(/background-color:\s*var\(--landing-cta\)/)
    expect(rule('.cta')).not.toMatch(/border-radius/)
    expect(rule('.cta:hover')).toMatch(/background-color:\s*var\(--landing-ink\)/)
  })

  it('draws the header and footer links as bordered controls, not a borderless patch (ORB-64)', () => {
    // The brand, "Accedi" and the footer links used to be `.top a, footer
    // .quiet-link` with a background colour and nothing else: no border, no
    // hover surface, and the brand caught the same treatment though it isn't a
    // control. This is the shell all three share now.
    const shell = css.match(
      /\.brand,\s*\.top \.quiet-link,\s*footer \.quiet-link\s*\{([^}]*)\}/,
    )?.[1]
    expect(shell, 'the header/footer control shell rule was not found').toBeTruthy()
    expect(shell).toMatch(/background-color:\s*var\(--landing-surface\)/)
    expect(shell).toMatch(/border-width:\s*2px/)
    // 44px, the touch-target floor: at the footer's smaller type the padding
    // alone falls short, so the floor is explicit rather than incidental.
    expect(shell).toMatch(/min-height:\s*2\.75rem/)
    // No shadow at this tier: that is `.box`/`.card`'s signal, not a link's.
    expect(shell).not.toMatch(/box-shadow/)

    const hover = css.match(
      /\.top \.quiet-link:hover,\s*footer \.quiet-link:hover\s*\{([^}]*)\}/,
    )?.[1]
    expect(hover, 'the header/footer hover rule was not found').toBeTruthy()
    expect(hover).toMatch(/background-color:\s*var\(--landing-ink\)/)
    expect(hover).toMatch(/color:\s*var\(--landing-cta-ink\)/)

    // The brand doesn't invert on hover the way the two links do: half its glyph
    // is drawn in the same colour as the ink ground, so it gets an offset shadow
    // instead, still without a blur.
    expect(rule('.brand:hover')).toMatch(/box-shadow:\s*4px 4px 0 var\(--landing-ink\)/)

    // The same class doubles as an inline citation in privacy.html and
    // terms.html's running text, which must stay underlined prose rather
    // than turn into a button mid-sentence: a later edit that moves the box
    // properties onto the generic rule would be the regression to catch.
    expect(rule('.quiet-link')).toMatch(/text-decoration:\s*underline/)
    expect(rule('.quiet-link')).not.toMatch(/border-width|min-height|display:/)
  })
})

describe('the wordmark is an asset, not a second webfont', () => {
  const names = ['wordmark.svg', 'wordmark-paper.svg', 'lockup.svg', 'lockup-paper.svg'] as const
  const svg = Object.fromEntries(
    names.map((name) => [
      name,
      readFileSync(fileURLToPath(import.meta.resolve(`@rebase/brand/${name}`)), 'utf-8'),
    ]),
  ) as Record<(typeof names)[number], string>
  // The palette's own values, so a colour changed in one place fails here rather
  // than leaving the generated assets a stale copy of it.
  const hex = Object.fromEntries(
    [...appTokens.matchAll(/(--color-[\w-]+):\s*([^;]+);/g)].map((match) => [match[1], match[2]?.trim()]),
  ) as Record<string, string>
  const ink = hex['--color-prussian-blue']
  const paper = hex['--color-paper']

  it('draws «rebase» as outlines, so no page loads a display face to read the name', () => {
    // The decision (docs/design/DECISIONS.md, 2026-09-14) is a face for the wordmark
    // and Outfit for everything else. A `<text>` element or a font-family in any of
    // these files would put Space Grotesk back on the critical path of every page,
    // and the name would reflow once it arrived.
    for (const name of names) {
      expect(svg[name], name).not.toMatch(/<text|font-family|@font-face/)
      expect(svg[name], name).toMatch(/<path[^>]+ d="M/)
    }
  })

  it('scales to whatever a consumer asks for: a viewBox and no fixed size', () => {
    // A social export at 1584x396 and the 18px header chip are the same file. A
    // width or height attribute here would make one of the two wrong.
    for (const name of names) {
      expect(svg[name], name).toMatch(/<svg[^>]+viewBox="0 0 [\d.]+ [\d.]+"/)
      expect(svg[name], name).not.toMatch(/<svg[^>]+(width|height)=/)
    }
  })

  it('signs both lockups with the same four tiles, in the same reading order', () => {
    // The fourth surface that draws the mark, after the app's component, the
    // website's `.glyph` and the favicon. An SVG opened as a file resolves no custom
    // property, so its hexes are literal, the way `rebase-logo.svg` already spells
    // them; what is new here is that a test holds them to `palette.css`. On a dark
    // ground the two ink tiles are the ground, which is the mark's own rule, so the
    // paper cut repaints exactly those two and invents no fifth colour.
    for (const [name, tileInk] of [
      ['lockup.svg', ink],
      ['lockup-paper.svg', paper],
    ] as const) {
      const drawn = [...svg[name].matchAll(/<rect[^>]+fill="([^"]+)"/g)].map((match) => match[1])
      const expected = BRAND_TILES.map((tile) =>
        tile === 'ink' ? tileInk : hex[BRAND_TILE_VARS[tile].replace(/var\(|\)/g, '')],
      )
      expect(expected, name).not.toContain(undefined)
      expect(drawn, name).toEqual(expected)
    }
  })

  it('inks the word with the palette, not with a colour of its own', () => {
    for (const [name, colour] of [
      ['wordmark.svg', ink],
      ['lockup.svg', ink],
      ['wordmark-paper.svg', paper],
      ['lockup-paper.svg', paper],
    ] as const) {
      expect(colour, name).toBeTruthy()
      const fills = [...svg[name].matchAll(/<path[^>]+fill="([^"]+)"/g)].map((match) => match[1])
      expect(fills, name).toEqual([colour])
    }
  })

  it('stands the mark on the word\'s baseline, at the proportions the README sells', () => {
    // The only arithmetic in the generator: the tile is half the cap height, the gap
    // three quarters of the tile, and the mark sits on the baseline. A regression in
    // `build-wordmark.py` would move those without touching a single colour, and the
    // lockup would still look like a lockup in a diff.
    for (const name of ['lockup.svg', 'lockup-paper.svg'] as const) {
      const rects = [...svg[name].matchAll(/<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)"/g)]
      const tiles = rects.map((match) => match.slice(1, 4).map(Number) as [number, number, number])
      const tile = tiles[0]?.[2] ?? 0
      expect(tile, name).toBeGreaterThan(0)
      // A 2x2 field of squares at the origin, and nothing outside the viewBox.
      expect(tiles.map(([x, y]) => [x, y]), name).toEqual([
        [0, 0],
        [tile, 0],
        [0, tile],
        [tile, tile],
      ])
      expect(svg[name], name).toMatch(/<rect[^>]+width="([\d.]+)" height="\1"/)
      const baseline = Number(svg[name].match(/translate\([-\d.]+ ([\d.]+)\)/)?.[1])
      const penX = Number(svg[name].match(/translate\(([-\d.]+) [-\d.]+\)/)?.[1])
      // `wordmark.svg` starts the word's ink at x=0, so its own pen offset is the
      // side bearing of the `r`: the difference between the two is where the ink
      // starts here, which is what the eye measures as the gap.
      const bearing = Number(svg['wordmark.svg'].match(/translate\(([-\d.]+) [-\d.]+\)/)?.[1])
      // The mark's bottom edge is the baseline the word sits on, and the word's ink
      // starts three quarters of a tile past the mark.
      expect(baseline, name).toBe(tile * 2)
      expect(penX - bearing, name).toBeCloseTo(tile * 2 + tile * 0.75, 1)
      const boxHeight = Number(svg[name].match(/viewBox="0 0 [\d.]+ ([\d.]+)"/)?.[1])
      expect(boxHeight, name).toBeGreaterThanOrEqual(baseline)
    }
  })

  it('draws the very same word in all four files', () => {
    // One run writes all four. Regenerating after a change to the face or the
    // tracking and committing three of them is the drift this catches, and so is a
    // hand-edit of one file, which the README forbids for this reason.
    const drawn = names.map((name) => svg[name].match(/ d="([^"]+)"/)?.[1])
    expect(drawn[0]).toBeTruthy()
    for (const [index, path] of drawn.entries()) {
      expect(path, names[index]).toBe(drawn[0])
    }
  })
})
