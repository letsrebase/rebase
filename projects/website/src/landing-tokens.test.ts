import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { AA_NON_TEXT, AA_TEXT, blendSrgb, contrastRatio, paletteFrom } from '@rebase/brand/contrast'

/** The palette, and the maths every surface measures its pairs with, both from
 *  `@rebase/brand` (REB-301). The site is the reference for the two applications, so
 *  the one thing that must not differ between their tests is the formula. */
const shared = paletteFrom(readFileSync(fileURLToPath(import.meta.resolve('@rebase/brand/palette.css')), 'utf-8'))
const landingCss = readFileSync(join(__dirname, 'landing.css'), 'utf-8')
const pitchCss = readFileSync(join(__dirname, 'pitch.css'), 'utf-8')
const pigrocrmCss = readFileSync(join(__dirname, 'pigrocrm.css'), 'utf-8')
const pigrocrmHtml = readFileSync(join(__dirname, 'pigrocrm.html'), 'utf-8')
const indexHtml = readFileSync(join(__dirname, 'index.html'), 'utf-8')
const privacyHtml = readFileSync(join(__dirname, 'privacy.html'), 'utf-8')
const termsHtml = readFileSync(join(__dirname, 'terms.html'), 'utf-8')

const LANDING_DECLARATION = /--landing-[\w-]+\s*:\s*[^;]+;/g
// pitch.css keeps its own short names (REB-248): the pattern reaches only these six
// declarations, never the many `var(--ink)`-style uses that follow them.
const PITCH_DECLARATION = /--(?:ink|quiet|paper|gold|melon-strong|melon)\s*:\s*[^;]+;/g
const VAR = /^var\((--color-[\w-]+)\)$/

function declaredValue(token: string, css: string): string {
  const match = css.match(new RegExp(`${token}\\s*:\\s*([^;]+);`))
  const value = match?.[1]
  if (!value) throw new Error(`${token} is not declared`)
  return value.trim()
}

/** Resolves a colour token declared as `var(--color-…)` in the given sheet to the
 *  sRGB hex a browser would compute, against either sheet: every text colour on the
 *  landing and on the deck is now a plain var() of a shared token, so there is no
 *  color-mix toward white left to resolve. `--landing-grid`/`--grid`,
 *  `--landing-cell`/`--cell` and `--landing-step`/`--step` are a line and lengths, not
 *  colours, and are not read through here. */
function resolveColour(token: string, css: string): string {
  const value = declaredValue(token, css)
  const direct = VAR.exec(value)
  if (!direct) throw new Error(`${token} is not var(--color-…): ${value}`)
  const hex = shared[direct[1] ?? '']
  if (!hex) throw new Error(`${token} points at ${direct[1]}, which tokens.css does not define`)
  return hex
}

const DIRECT_HEX = /^#[0-9a-fA-F]{3,8}$/
const LANDING_VAR = /^var\((--landing-[\w-]+)\)$/

/** Every `--landing-*` custom property declared in the given sheet's `:root`,
 *  resolved to a plain hex where that is possible without a backdrop: a
 *  `var(--color-…)` value through `shared`, or a literal `#ffffff` as-is. A
 *  `color-mix()` (the grid line, the tile, the two on-ink overlays, the light
 *  band's veil) is left to `resolveValue`, which has the backdrop to composite it
 *  against. */
function landingVars(css: string): Record<string, string> {
  const vars: Record<string, string> = {}
  const root = css.match(/:root\s*\{([^}]*)\}/)?.[1] ?? ''
  for (const declaration of root.matchAll(/(--landing-[\w-]+)\s*:\s*([^;]+);/g)) {
    const name = declaration[1]
    const value = declaration[2]?.trim()
    if (!name || !value) continue
    if (DIRECT_HEX.test(value)) {
      vars[name] = value.toLowerCase()
      continue
    }
    const colourVar = VAR.exec(value)
    if (colourVar) {
      const hex = shared[colourVar[1] ?? '']
      if (hex) vars[name] = hex
    }
  }
  return vars
}

/** Every `--landing-*` declaration's raw value, unresolved: `resolveValue` reads
 *  this for the ones `landingVars` could not (a `color-mix()`), because resolving
 *  one of those needs a backdrop it does not have until an element is being
 *  walked. */
function landingVarsRaw(css: string): Record<string, string> {
  const raw: Record<string, string> = {}
  const root = css.match(/:root\s*\{([^}]*)\}/)?.[1] ?? ''
  for (const declaration of root.matchAll(/(--landing-[\w-]+)\s*:\s*([^;]+);/g)) {
    const name = declaration[1]
    const value = declaration[2]?.trim()
    if (name && value) raw[name] = value
  }
  return raw
}

/** Both `--landing-*` on-ink/veil tokens are `color-mix(in <space>, var(--color-…)
 *  N%, transparent)`; resolving one exactly would mean reproducing the browser's
 *  OKLab math, so this blends in sRGB instead, close enough at these percentages to
 *  place a ratio on the right side of 4.5 without claiming false precision. */
const COLOR_MIX = /^color-mix\(in [\w-]+, var\((--color-[\w-]+)\) (\d+(?:\.\d+)?)%, transparent\)$/

/** Strips comments and every `@media`/`@font-face`/`@keyframes` block: REB-267's own
 *  `@media (min-width: 60rem)` addition to `pigrocrm.css` carries a `min-block-size`,
 *  no colour, and stripping it here keeps the one-rule-at-a-time scan below from
 *  having to nest braces for a block it would find nothing in anyway. */
function stripAtRules(css: string): string {
  return css
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/@(?:media|font-face|keyframes)[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, '')
}

type ColourRule = { selector: string; value: string }

/** A declared value resolved to a plain hex: a literal `#rgb`/`#rrggbb`; a
 *  `var(--landing-…)` through `vars` directly, or through `rawVars` and
 *  `COLOR_MIX` when it needs `backdrop` to composite against; `transparent`/`none`
 *  as `backdrop` itself, since that is precisely the case where the ancestor's
 *  ground is what paints. Undefined when none of those apply (a `color-mix()`
 *  with no backdrop yet, or a value this derivation does not understand) --
 *  callers treat that as "skip this element", the wrong colour being worse than no
 *  pair. */
function resolveValue(
  value: string,
  vars: Record<string, string>,
  rawVars: Record<string, string>,
  backdrop: string | undefined,
): string | undefined {
  if (value === 'transparent' || value === 'none') return backdrop
  if (DIRECT_HEX.test(value)) {
    const hex = value.toLowerCase()
    return hex.length === 4 ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}` : hex
  }
  const name = LANDING_VAR.exec(value)?.[1] ?? ''
  if (vars[name]) return vars[name]
  if (!backdrop) return undefined
  const mix = COLOR_MIX.exec(rawVars[name] ?? '')
  const inner = mix && shared[mix[1] ?? '']
  return inner ? blendSrgb(inner, backdrop, Number(mix[2])) : undefined
}

/** Every rule in `css` that declares the given property, next to the selector list
 *  it was declared on, value left unresolved -- the building blocks the DOM walk
 *  below crosses against `pigrocrm.html`'s real markup, so a new `color` or
 *  `background` `pigrocrm.css` adds is read the next run rather than typed in here. */
function extractDeclarations(css: string, property: 'color' | 'background'): ColourRule[] {
  const pattern = property === 'color' ? /(?:^|;)\s*color\s*:\s*([^;]+);/ : /background(?:-color)?\s*:\s*([^;]+);/
  const rules: ColourRule[] = []
  for (const rule of stripAtRules(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = rule[1]?.trim()
    const body = rule[2]
    if (!selector || selector === ':root' || !body) continue
    const value = body.match(pattern)?.[1]?.trim()
    if (!value) continue
    rules.push({ selector, value })
  }
  return rules
}

/** CSS specificity of a selector list, the highest among its comma-separated
 *  alternatives (`.matches()` succeeds on any one of them, and that is the
 *  alternative actually competing in the cascade for this element). Neither sheet
 *  uses an id or `!important`, so ids are counted for completeness but never seen;
 *  a pseudo-element counts for nothing; it selects a box no text rule here targets. */
function specificity(selectorList: string): number {
  const scores = selectorList.split(',').map((selector) => {
    const tokens = selector.match(/::[\w-]+|:[\w-]+(?:\([^)]*\))?|#[\w-]+|\.[\w-]+|\[[^\]]*\]|[A-Za-z][\w-]*/g) ?? []
    let score = 0
    for (const token of tokens) {
      if (token.startsWith('::')) continue
      else if (token.startsWith('#')) score += 100
      else if (token.startsWith('.') || token.startsWith(':') || token.startsWith('[')) score += 10
      else score += 1
    }
    return score
  })
  return Math.max(0, ...scores)
}

/** The rule that would actually paint on `element`: the highest-specificity match
 *  among `rules`, ties broken by array order (`landing.css` then `pigrocrm.css`, the
 *  order both call sites below build these in, is the order the cascade loads them
 *  in). Without this, `landing.css`'s base `p, li { color: var(--landing-ink-quiet) }`
 *  (line 256) would pair with every `<p class="kicker">` too, alongside the
 *  `.kicker` rule that actually wins there -- a real cascade conflict, not a
 *  contrast bug. */
function winningRule(rules: ColourRule[], element: Element): ColourRule | undefined {
  let best: { rule: ColourRule; specificity: number; index: number } | undefined
  rules.forEach((rule, index) => {
    let matches = false
    try {
      matches = element.matches(rule.selector)
    } catch {
      // A selector jsdom cannot evaluate (an unsupported pseudo-class) describes no
      // static element, so it wins nothing here.
    }
    if (!matches) return
    const score = specificity(rule.selector)
    if (!best || score > best.specificity || (score === best.specificity && index > best.index)) {
      best = { rule, specificity: score, index }
    }
  })
  return best?.rule
}

/** For every element some `colourRules` entry could apply to, the pair it actually
 *  renders. The background is the whole ancestor-or-self chain of winning
 *  `background` rules, folded outermost first so each level composites over what
 *  is actually behind it (`transparent`/`background: none` pass the level below
 *  through unpainted, a `color-mix()` like the light band's veil blends against
 *  it) -- not just the nearest one, which would treat a `color-mix()` ground as
 *  unresolvable and skip every light-band element rather than composite it. The
 *  colour then resolves against that same background, since a translucent text
 *  colour (`.band.dark .who`'s on-ink overlay) composites the same way. Undefined
 *  anywhere in either chain drops the element rather than guessing -- the wrong
 *  colour is worse than no pair. Returns `[text, background, element]` triples, the
 *  element kept so a caller can assert a specific one was reached rather than
 *  only that the aggregate list is non-empty. */
function derivePairs(
  html: string,
  colourRules: ColourRule[],
  backgroundRules: ColourRule[],
  vars: Record<string, string>,
  rawVars: Record<string, string>,
): [string, string, Element][] {
  document.body.innerHTML = html.match(/<body[^>]*>([\s\S]*)<\/body>/)?.[1] ?? ''
  function resolveBackground(el: Element): string | undefined {
    const chain: ColourRule[] = []
    for (let node: Element | null = el; node; node = node.parentElement) {
      const winner = winningRule(backgroundRules, node)
      if (winner) chain.push(winner)
    }
    let backdrop: string | undefined
    for (let i = chain.length - 1; i >= 0; i--) {
      backdrop = resolveValue(chain[i]!.value, vars, rawVars, backdrop)
      if (backdrop === undefined) return undefined
    }
    return backdrop
  }
  const candidates = new Set<Element>()
  for (const rule of colourRules) {
    try {
      document.querySelectorAll(rule.selector).forEach((element) => candidates.add(element))
    } catch {
      // A selector jsdom cannot evaluate (an unsupported pseudo-class) describes no
      // static element, so it contributes none here.
    }
  }
  // Not deduped by colour+background: `.who`'s pair happens to equal `.chat-tool
  // dt`'s, and a caller below asserts on the specific element a selector reaches,
  // which a first-one-wins dedup would silently hide behind whichever candidate
  // the `Set` iterates first.
  const pairs: [string, string, Element][] = []
  for (const element of candidates) {
    const background = resolveBackground(element)
    if (background === undefined) continue
    const winner = winningRule(colourRules, element)
    const colour = winner && resolveValue(winner.value, vars, rawVars, background)
    if (!colour) continue
    pairs.push([colour, background, element])
  }
  document.body.innerHTML = ''
  return pairs
}

describe('landing tokens', () => {
  it('resolves every --landing-* colour out of the shared palette', () => {
    expect(resolveColour('--landing-surface', landingCss)).toBe('#f1f2f3')
    expect(resolveColour('--landing-ink', landingCss)).toBe('#011936')
    expect(resolveColour('--landing-ink-quiet', landingCss)).toBe('#465362')
    expect(resolveColour('--landing-cta', landingCss)).toBe('#e5133e')
    expect(resolveColour('--landing-focus', landingCss)).toBe('#ed254e')
    // REB-276: the accent-as-text token, the palette's deep watermelon.
    expect(resolveColour('--landing-accent', landingCss)).toBe('#c50d33')
  })

  it('contains no raw hexadecimal in the --landing-* block, other than white', () => {
    // White is the neutral a tint is mixed toward, not a sixth colour. Everything
    // else must be a var(--color-…) or a color-mix() of one, which is what makes
    // forking the palette mechanically impossible rather than discouraged.
    for (const declaration of landingCss.match(LANDING_DECLARATION) ?? []) {
      for (const hex of declaration.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []) {
        expect(hex.toLowerCase(), `raw hex in ${declaration}`).toBe('#ffffff')
      }
    }
  })

  it('reaches 4.5:1 on every text pair', () => {
    const surface = resolveColour('--landing-surface', landingCss)
    const ink = resolveColour('--landing-ink', landingCss)
    const quiet = resolveColour('--landing-ink-quiet', landingCss)
    const accent = resolveColour('--landing-accent', landingCss)
    // Boxes and cards are opaque white, so every text colour is also read on white.
    for (const [text, background] of [
      [ink, surface],
      [quiet, surface],
      [ink, '#ffffff'],
      [quiet, '#ffffff'],
      ['#ffffff', resolveColour('--landing-cta', landingCss)],
      // REB-276: the kicker/accent text pair this sheet used to render in
      // `--landing-cta`, which reads 4.17:1 on the light band's ground. The veil
      // is paper at four fifths over the paper body, so it resolves to paper;
      // the full compositing derivation lives in `landing.css text pairs` below.
      [accent, surface],
      [accent, '#ffffff'],
      [resolveColour('--landing-gold', landingCss), ink],
    ] as const) {
      expect(contrastRatio(text, background), `${text} on ${background}`).toBeGreaterThanOrEqual(AA_TEXT)
    }
  })

  it('reaches 3:1 on the focus ring, which is a component and not text', () => {
    const ratio = contrastRatio(
      resolveColour('--landing-focus', landingCss),
      resolveColour('--landing-surface', landingCss),
    )
    expect(ratio).toBeGreaterThanOrEqual(AA_NON_TEXT)
  })

  it('never uses raw Watermelon as a solid fill', () => {
    // The regression banned by name. In the app this is solved: --primary and
    // --destructive point at --color-watermelon-deep (REB-307) and the CRM sidebar's
    // active tile at --color-watermelon-strong, because white on raw Watermelon is
    // 4.221:1 and misses the 4.5:1 body-text floor. The landing must not re-introduce
    // it on the one element that IS a solid fill under white text: the call to action,
    // which fills with `-strong` and is measured as a text pair above.
    for (const [, property, value] of landingCss.matchAll(
      /(?:^|[;{])\s*(background|background-color|fill)\s*:\s*([^;}]+)/g,
    )) {
      expect(value, `${property} fills with raw Watermelon`).not.toMatch(
        /--color-watermelon(?!-strong)/,
      )
      expect(value, `${property} fills with #ed254e`).not.toMatch(/#ed254e/i)
    }
  })

  it('loads no webfont other than Outfit, and none from a CDN', () => {
    expect(landingCss).not.toMatch(/Reenie/i)
    expect(landingCss).not.toMatch(/fonts\.googleapis\.com|fonts\.gstatic\.com/)
    // The sheet must not declare a face of its own: the typeface is the brand's and
    // arrives from `@rebase/brand/font.css`, prepended by the palette plugin. Two
    // @font-face blocks for one family is how a landing ends up on a stale copy.
    expect(landingCss).not.toMatch(/@font-face/)
    // Comments stripped first: that file's own comment explains at length why it does
    // not fetch from Google, and the check is about what the browser requests.
    const brandFont = readFileSync(
      fileURLToPath(import.meta.resolve('@rebase/brand/font.css')),
      'utf-8',
    ).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(brandFont).not.toMatch(/fonts\.googleapis\.com|fonts\.gstatic\.com/)
    const families = [...brandFont.matchAll(/@font-face\s*\{[^}]*font-family:\s*'([^']+)'/g)].map(
      (m) => m[1],
    )
    expect(families).toEqual(['Outfit'])
  })
})

// REB-276: the deck's kicker and `.accent` word read 4.17:1 on the light band's
// veil when no white `.box` sat under them (/pigrocrm's `#voci` heading is the
// case PR #186's review found; `--landing-cta` only clears AA as a fill under
// white). The fix moved the text to `--landing-accent`; what stops it coming
// back is this block, which derives every pair `landing.css` alone paints
// crossed against the real markup of the pages that load it (pigrocrm.css's
// overrides left to the suite below, which reads both sheets), instead of
// naming pairs by hand.
describe('landing.css text pairs', () => {
  const vars = landingVars(landingCss)
  const rawVars = landingVarsRaw(landingCss)
  const colourRules = extractDeclarations(landingCss, 'color')
  const backgroundRules = extractDeclarations(landingCss, 'background')

  const pairs = [indexHtml, pigrocrmHtml, privacyHtml, termsHtml].flatMap((html) =>
    derivePairs(html, colourRules, backgroundRules, vars, rawVars).filter(
      // Elements `pigrocrm.css` also reaches belong to the suite below: its
      // cascade, not `landing.css` alone, decides what paints there.
      ([, , element]) => !touchesPigrocrmCss(element),
    ),
  )

  it('reaches the bare kicker on the light band specifically, not just a non-empty list', () => {
    // The exact element REB-276 observed: a `.deck .kicker` sitting directly
    // on the band's veil, no `.box` under it. The veil is Paper at 80% over
    // transparent, so over the Paper body it composites to Paper and the pair
    // is the one the card measured at 4.17:1. Where the fixed field canvas
    // drifts a tile under the veil the real ground is darker, for every text
    // colour on a light band alike: the deck's design accepts that (the veil
    // exists so the tiles show through, and `prefers-contrast: more` turns the
    // canvas off), and this contract measures the body ground, not the canvas.
    // If the derivation ever stopped resolving `--landing-veil`, this fails on
    // the spot rather than the loop below passing quietly on whatever it found.
    const voci = pairs.find(([, , element]) => element.id === 'voci')
    expect(voci).toBeDefined()
    expect(voci?.[0]).toBe('#c50d33')
    expect(voci?.[1]).toBe('#f1f2f3')
    // And not only this one page: index.html carries the same bare kicker on
    // its two light bands.
    expect(pairs.filter(([, , element]) => element.matches('.band:not(.dark) p.kicker'))).toHaveLength(3)
  })

  it('reaches 4.5:1 on every text pair landing.css renders alone', () => {
    expect(pairs.length).toBeGreaterThan(0)
    for (const [text, background] of pairs) {
      expect(contrastRatio(text, background), `${text} on ${background}`).toBeGreaterThanOrEqual(AA_TEXT)
    }
  })

  it('would have caught REB-276: the accent text back in --landing-cta on the veil', () => {
    // The regression drill, same shape as REB-268's: put the pre-fix value
    // into a copy of the real sheet, run it through the same extraction and
    // derivation, and the `#voci` pair must come out failing. White (inside a
    // `.box`) is the passing direction this drill also states: `-strong` was
    // never wrong there, which is why the hero's own kicker hid the defect.
    const preFixCss = landingCss.replaceAll(
      'color: var(--landing-accent);',
      'color: var(--landing-cta);',
    )
    // Both rule lists come from the substituted sheet, so the drill is
    // hermetic even if a future regression touches a background too. `vars`
    // and `rawVars` are the `:root` block, which the substitution above
    // cannot reach (its pattern matches only `color:` declarations).
    const preFixAll = derivePairs(
      pigrocrmHtml,
      extractDeclarations(preFixCss, 'color'),
      extractDeclarations(preFixCss, 'background'),
      vars,
      rawVars,
    )
    const failingVoci = preFixAll.find(
      ([, , element]) => element.id === 'voci' && !touchesPigrocrmCss(element),
    )
    expect(failingVoci?.[0]).toBe('#e5133e')
    expect(contrastRatio(failingVoci![0], failingVoci![1])).toBeLessThan(AA_TEXT)

    const heroKicker = preFixAll.find(([, , element]) => element.matches('.hero .box .kicker'))
    expect(heroKicker?.[0]).toBe('#e5133e')
    expect(contrastRatio(heroKicker![0], heroKicker![1])).toBeGreaterThanOrEqual(AA_TEXT)
  })
})

// pitch.css joined TOKEN_CONSUMERS in REB-248: it used to restate the six colours and
// its own @font-face, a latent fork of shared/brand that a palette change would have
// left the deck on. These hold the same two guarantees landing.css already had.
describe('pitch deck tokens', () => {
  it('resolves every pitch colour variable out of the shared palette', () => {
    expect(resolveColour('--ink', pitchCss)).toBe('#011936')
    expect(resolveColour('--quiet', pitchCss)).toBe('#465362')
    expect(resolveColour('--paper', pitchCss)).toBe('#f1f2f3')
    expect(resolveColour('--gold', pitchCss)).toBe('#f9dc5c')
    expect(resolveColour('--melon', pitchCss)).toBe('#ed254e')
    expect(resolveColour('--melon-strong', pitchCss)).toBe('#e5133e')
  })

  it('contains no raw hexadecimal in its colour variables, other than white', () => {
    for (const declaration of pitchCss.match(PITCH_DECLARATION) ?? []) {
      for (const hex of declaration.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []) {
        expect(hex.toLowerCase(), `raw hex in ${declaration}`).toBe('#ffffff')
      }
    }
  })

  it('declares no @font-face of its own, since palette-plugin.ts prepends the brand font', () => {
    expect(pitchCss).not.toMatch(/@font-face/)
    expect(pitchCss).toMatch(/font-family:\s*var\(--font-sans\)/)
  })
})

/** Every selector `pigrocrm.css` names for *any* property, not only the ones
 *  that win a `color`/`background`: `.voices-light` sets neither today, only
 *  `border-top-color` and a `data-reveal` reset, so a filter over winning colour
 *  or background rules would never reach the element REB-268 is about at all.
 *  Module level since REB-276: the landing-only block above it uses the same
 *  test to leave `pigrocrm.css`'s pairs to their own suite. */
const pigrocrmSelectors = [...stripAtRules(pigrocrmCss).matchAll(/([^{}]+)\{/g)]
  .map((rule) => rule[1]?.trim())
  .filter((selector): selector is string => Boolean(selector) && selector !== ':root')

/** Whether `element` or an ancestor is named by any `pigrocrm.css` selector --
 *  the line between the pairs `pigrocrm.css` participates in and the ones
 *  `landing.css` alone renders. */
function touchesPigrocrmCss(element: Element): boolean {
  for (let node: Element | null = element; node; node = node.parentElement) {
    for (const selector of pigrocrmSelectors) {
      let matches = false
      try {
        matches = node.matches(selector)
      } catch {
        // Same unsupported-pseudo-class case as elsewhere: names no element.
      }
      if (matches) return true
    }
  }
  return false
}
// REB-268: /pigrocrm read 4.37:1 on the light band's role line, `.voices-light`
// overriding the ground `landing.css`'s `.who .role` sits on -- a pair the test
// above never saw, because it reads `landing.css` alone. These derive every pair
// `pigrocrm.css` participates in (its own declarations, or a `landing.css` one
// applied to an element whose colour or background chain `pigrocrm.css` also
// touches) crossed against `pigrocrm.html`'s real DOM, instead of naming them, so
// the next override is caught unseen or not at all. A pair neither sheet's rules
// touch (`landing.css`'s own kicker-on-veil, say) is `landing tokens`' concern
// above, not this one, and is left out so this suite does not fail on a finding
// outside REB-268's ground.
describe('pigrocrm.css text pairs', () => {
  const vars = landingVars(landingCss)
  const rawVars = landingVarsRaw(landingCss)
  const landingColourRules = extractDeclarations(landingCss, 'color')
  const landingBackgroundRules = extractDeclarations(landingCss, 'background')
  const pigrocrmColourRules = extractDeclarations(pigrocrmCss, 'color')
  const pigrocrmBackgroundRules = extractDeclarations(pigrocrmCss, 'background')
  const colourRules = [...landingColourRules, ...pigrocrmColourRules]
  const backgroundRules = [...landingBackgroundRules, ...pigrocrmBackgroundRules]

  const allPairs = derivePairs(pigrocrmHtml, colourRules, backgroundRules, vars, rawVars)
  const pairs = allPairs.filter(([, , element]) => touchesPigrocrmCss(element))

  it('reaches the .who text on .voices-light specifically, not just a non-empty list', () => {
    // A derivation that silently stopped reaching this element -- a regex change,
    // a selector jsdom cannot evaluate, a background it cannot resolve -- would
    // still pass every assertion below on whatever it does find. `.voices-light`
    // itself carries no colour or background of its own today (`.box` supplies the
    // white), so `touchesPigrocrmCss` reaches it only through the ancestor chain,
    // exactly the path REB-268 is about. `.who` is the candidate, not its `.role`
    // child: `.role`'s own rule only sets `font-size`, so its colour is `.who`'s,
    // inherited, and `derivePairs` (like the browser) reports the colour against
    // the element a `color` rule actually names.
    const who = pairs.find(([, , element]) => element.matches('.voices-light .who'))
    expect(who).toBeDefined()
    expect(who?.[0]).toBe('#465362')
    expect(who?.[1]).toBe('#ffffff')
  })

  it('reaches 4.5:1 on every text pair pigrocrm.css participates in', () => {
    for (const [text, background] of pairs) {
      expect(contrastRatio(text, background), `${text} on ${background}`).toBeGreaterThanOrEqual(AA_TEXT)
    }
  })

  it('would have caught REB-268: a background pigrocrm.css adds under an existing landing.css text colour', () => {
    // The regression drill, through the real pipeline rather than a hand-built
    // stand-in: appends the exact shape REB-268 named -- `.voices-light` itself
    // taking a background -- to the real `pigrocrm.css` text, extracts it the same
    // way the describe block above does, and derives against the real
    // `pigrocrm.html`. Grey is the failing "old value" this drill proves the
    // mechanism would have caught; white (what `.box` already supplies today) is
    // the "new" one it passes.
    const failingCss = `${pigrocrmCss}\n.voices-light { background-color: #9aa0a6; }\n`
    const failingBackgrounds = [...landingBackgroundRules, ...extractDeclarations(failingCss, 'background')]
    const failing = derivePairs(pigrocrmHtml, colourRules, failingBackgrounds, vars, rawVars)
    const failingWho = failing.find(([, , element]) => element.matches('.voices-light .who'))
    expect(failingWho?.[1]).toBe('#9aa0a6')
    expect(contrastRatio(failingWho![0], failingWho![1])).toBeLessThan(AA_TEXT)

    const passingCss = `${pigrocrmCss}\n.voices-light { background-color: #ffffff; }\n`
    const passingBackgrounds = [...landingBackgroundRules, ...extractDeclarations(passingCss, 'background')]
    const passing = derivePairs(pigrocrmHtml, colourRules, passingBackgrounds, vars, rawVars)
    const passingWho = passing.find(([, , element]) => element.matches('.voices-light .who'))
    expect(contrastRatio(passingWho![0], passingWho![1])).toBeGreaterThanOrEqual(AA_TEXT)
  })
})
