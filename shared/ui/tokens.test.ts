import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * The contract of the one token layer both applications render on. It reads the
 * stylesheet as text, resolves every colour back through the brand palette (so a
 * hand-mixed hex is mechanically impossible rather than discouraged) and pins the
 * rules of the application-variant record of 2026-09-18: radius zero, ink lines, one
 * offset shadow with no blur on what floats, nothing on what rests, one colour scheme.
 *
 * It lives here and runs once, instead of the two near-identical copies the two
 * applications carried until REB-299.
 */
const tokensCss = readFileSync(join(__dirname, 'tokens.css'), 'utf-8')
const brandCss = readFileSync(
  fileURLToPath(import.meta.resolve('@rebase/brand/palette.css')),
  'utf-8',
)
const css = `${brandCss}\n${tokensCss}`

function hexToRgb(hex: string): [number, number, number] {
  const value = hex.replace('#', '')
  return [parseInt(value.slice(0, 2), 16), parseInt(value.slice(2, 4), 16), parseInt(value.slice(4, 6), 16)]
}

function relativeLuminance([r, g, b]: [number, number, number]): number {
  const channel = (c: number) => {
    const srgb = c / 255
    return srgb <= 0.03928 ? srgb / 12.92 : Math.pow((srgb + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

/** WCAG 2.x contrast ratio between two colours, order-independent. */
function contrastRatio(hexA: string, hexB: string): number {
  const a = relativeLuminance(hexToRgb(hexA))
  const b = relativeLuminance(hexToRgb(hexB))
  const lighter = Math.max(a, b)
  const darker = Math.min(a, b)
  return (lighter + 0.05) / (darker + 0.05)
}

/** Reads whatever the palette currently assigns a colour token, so every contrast
 *  figure below is recomputed from the live value on every run. A future edit to a hex
 *  is what this checks; a hand-written expected ratio would not notice. */
function tokenHex(name: string): string {
  const match = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`))
  const hex = match?.[1]
  if (!hex) throw new Error(`token --color-${name} not found in tokens.css`)
  return hex
}

/* --- Oklab, so the chart tokens can be *computed* rather than recorded by hand ---
   The five --chart-* tokens are `color-mix(in oklab, ...)` expressions, and a test that
   only carried their expected sRGB values as a comment ("recompute these in a browser if
   the expression changes") is a pair that drifts the first time nobody does. Everything
   below is the CSS Color 4 definition of that mix, verified against Chromium's own
   `getComputedStyle` output for all five expressions: it agrees to the byte. */

const srgbToLinear = (c: number): number =>
  c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
const linearToSrgb = (c: number): number =>
  c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055

type Triple = [number, number, number]

function hexToLinear(hex: string): Triple {
  return hexToRgb(hex).map((c) => srgbToLinear(c / 255)) as Triple
}

function linearToOklab([r, g, b]: Triple): Triple {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}

function oklabToLinear([lightness, a, b]: Triple): Triple {
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ]
}

function linearToHex(linear: Triple): string {
  return `#${linear
    .map((c) => Math.round(Math.min(1, Math.max(0, linearToSrgb(c))) * 255).toString(16).padStart(2, '0'))
    .join('')}`
}

function oklabOf(hex: string): Triple {
  return linearToOklab(hexToLinear(hex))
}

/** Euclidean distance in Oklab x100, the same units the dataviz palette gates use. */
function deltaE(hexA: string, hexB: string): number {
  const a = oklabOf(hexA)
  const b = oklabOf(hexB)
  return Math.hypot(...a.map((v, i) => (v - b[i]!) * 100))
}

/** A colour as it may appear inside a `color-mix()`: a tint reference or a literal. */
function resolveMixOperand(text: string): string {
  const reference = text.match(/^var\(--color-([a-z-]+)\)$/)
  if (reference) return tokenHex(reference[1]!)
  if (/^#[0-9a-fA-F]{6}$/.test(text)) return text
  throw new Error(`unresolvable colour operand in tokens.css: ${text}`)
}

const OPERAND = String.raw`(?:var\(--color-[a-z-]+\)|#[0-9a-fA-F]{6})`

/**
 * Evaluates `color-mix(in oklab, <a> <p>%, <b>)` exactly as a browser does, so the
 * recorded sRGB value of every chart token is derived from the declaration in
 * `tokens.css` instead of asserted against a number somebody typed.
 */
function evaluateChartToken(token: string): string {
  const declaration = css.match(new RegExp(`${token}:\\s*([^;]+);`))
  if (!declaration) throw new Error(`${token} not found in tokens.css`)
  const mix = declaration[1]!
    .trim()
    .match(new RegExp(String.raw`^color-mix\(in oklab,\s*(${OPERAND})\s+(\d{1,3})%,\s*(${OPERAND})\s*\)$`))
  if (!mix) throw new Error(`${token} is not a two-operand oklab color-mix: ${declaration[1]}`)
  const first = oklabOf(resolveMixOperand(mix[1]!))
  const second = oklabOf(resolveMixOperand(mix[3]!))
  const weight = parseInt(mix[2]!, 10) / 100
  const mixed = first.map((v, i) => v * weight + second[i]! * (1 - weight)) as Triple
  return linearToHex(oklabToLinear(mixed))
}

/** Every declaration of a selector in tokens.css, concatenated in file order. A
 *  selector may legitimately appear more than once, and a non-global match would
 *  silently see only the first, exempting every later occurrence. */
function block(selector: string): string {
  const escaped = selector.replace(/[.[\]*+?^${}()|\\]/g, '\\$&')
  const bodies = [
    ...css.matchAll(new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([\\s\\S]*?)\\n\\s*\\}`, 'g')),
  ].map((match) => match[1]!)
  if (bodies.length === 0) throw new Error(`rule "${selector}" not found in tokens.css`)
  return bodies.join('\n')
}

/** The value one token is assigned inside one rule, by exact selector. */
function declaration(selector: string, token: string): string {
  const value = block(selector).match(new RegExp(`${token}:\\s*([^;]+);`))?.[1]
  if (!value) throw new Error(`${token} is not declared in "${selector}"`)
  return value.trim()
}

describe('the palette every slot resolves through', () => {
  it.each([
    ['watermelon', '#ed254e'],
    ['watermelon-strong', '#e5133e'],
    ['royal-gold', '#f9dc5c'],
    ['paper', '#f1f2f3'],
    ['prussian-blue', '#011936'],
    ['charcoal-blue', '#465362'],
  ])('defines %s as %s', (name, hex) => {
    expect(css).toContain(`--color-${name}: ${hex}`)
  })

  it('uses the accessible Watermelon variant as the primary and destructive colour', () => {
    // Raw --color-watermelon stays the brand colour for accents, borders and the focus
    // ring; a solid fill carrying white text needs the darker, AA-compliant variant.
    expect(css).toMatch(/--primary:\s*var\(--color-watermelon-strong\)/)
    expect(css).toMatch(/--destructive:\s*var\(--color-watermelon-strong\)/)
  })

  it('white text on watermelon-strong clears the 4.5:1 AA text threshold', () => {
    expect(contrastRatio('#ffffff', tokenHex('watermelon-strong'))).toBeGreaterThanOrEqual(4.5)
  })

  it('carries Paper text on the blue sidebar at AA', () => {
    expect(contrastRatio(tokenHex('paper'), tokenHex('prussian-blue'))).toBeGreaterThanOrEqual(4.5)
  })

  it('loads no webfont other than Outfit, and declares the face nowhere but brand', () => {
    expect(css).not.toMatch(/Reenie/i)
    expect(tokensCss).not.toMatch(/@font-face/)
    const brandFont = readFileSync(
      fileURLToPath(import.meta.resolve('@rebase/brand/font.css')),
      'utf-8',
    )
    const families = [...brandFont.matchAll(/@font-face\s*\{[^}]*font-family:\s*'([^']+)'/g)].map(
      (m) => m[1],
    )
    expect(families).toEqual(['Outfit'])
  })

  it('mixes only the palette, white and transparent, everywhere in the file', () => {
    // A sixth colour cannot enter through a border, a shadow, a chart hue or a sidebar
    // slot: every mix in the file resolves to a tint of the six.
    const mixes = [...css.matchAll(/color-mix\(in oklab,([^;]*?)\)\s*(?:;|,)/g)].map((m) => m[1]!)
    expect(mixes.length).toBeGreaterThan(8)
    for (const mix of mixes) {
      for (const hex of mix.match(/#[0-9a-fA-F]{3,8}/g) ?? []) {
        expect(hex.toLowerCase(), `hex inside color-mix(${mix})`).toBe('#ffffff')
      }
      for (const [, name] of mix.matchAll(/var\(--([a-z0-9-]+)\)/g)) {
        expect(name, `var inside color-mix(${mix})`).toMatch(/^color-/)
      }
    }
  })
})

/* --- The squared system, one weight lighter (the record of 2026-09-18) -------------
   Until that day the applications drew the soft system: 10px radius, a 12% line, four
   blurred shadows, no grid. Every assertion below is one sentence of the record's § 3,
   so an edit that walks it back has to mean it. */
describe('the squared shapes', () => {
  it('zeroes --radius, and with it the whole derived scale', () => {
    expect(declaration('@theme', '--radius')).toMatch(/^0(px)?$/)
    expect(css).toMatch(/--radius-lg:\s*var\(--radius\)/)
    for (const [rung, factor] of [
      ['sm', '0.6'],
      ['md', '0.8'],
      ['xl', '1.4'],
      ['2xl', '1.8'],
      ['3xl', '2.2'],
      ['4xl', '2.6'],
    ]) {
      expect(css, `--radius-${rung}`).toContain(`--radius-${rung}: calc(var(--radius) * ${factor})`)
    }
  })

  it('draws the line as the ink itself, never a tint of it', () => {
    // The weight of a line now comes from its width, 1px against the site's 2px, not
    // from fading the colour: the 12% border and 20% input of the soft system are gone.
    for (const token of ['--border', '--input']) {
      expect(declaration(':root', token), token).toBe('var(--color-prussian-blue)')
    }
  })

  it('names both widths, so no component types 1px or 2px', () => {
    expect(declaration(':root', '--line')).toBe('1px')
    expect(declaration(':root', '--line-strong')).toBe('2px')
  })

  it('casts one ink step of 4px, half the size of the site own step', () => {
    expect(declaration(':root', '--step')).toBe('4px')
  })

  it('points every @theme inline --shadow-* at a bare var(), never a compound value', () => {
    // Tailwind v4 decomposes a compound theme value at build time, baking the lengths
    // into every utility and leaving only the innermost var() live, so a scope
    // overriding --shadow-xs itself never reaches a rendered element. The hub's .site
    // scope repoints these indirections; this is what keeps them repointable.
    for (const step of ['xs', 'sm', 'md', 'lg', 'xl', '2xl']) {
      expect(declaration('@theme inline', `--shadow-${step}`), `--shadow-${step}`).toMatch(
        /^var\(--shadow-app-(?:xs|sm|md|lg)\)$/,
      )
    }
  })

  it('leaves a resting surface with no shadow at all', () => {
    // A card, a table and a page header sit on the page with their border. These two
    // rungs are what the generated primitives ask for as shadow-xs and shadow-sm.
    expect(declaration(':root', '--shadow-app-xs')).toBe('none')
    expect(declaration(':root', '--shadow-app-sm')).toBe('none')
  })

  it('casts a floating surface as an ink offset, never a blur or a tint', () => {
    for (const step of ['md', 'lg']) {
      const value = declaration(':root', `--shadow-app-${step}`)
      const parts = value.split(' ')
      expect(parts, `--shadow-app-${step}`).toHaveLength(4)
      expect(parts[0], `--shadow-app-${step} offset-x`).toBe('var(--step)')
      expect(parts[1], `--shadow-app-${step} offset-y`).toBe('var(--step)')
      // The third length is the blur radius, held at exactly zero.
      expect(parts[2], `--shadow-app-${step} blur`).toBe('0')
      expect(parts[3], `--shadow-app-${step} colour`).toBe('var(--color-prussian-blue)')
    }
    // The low-opacity tints the soft system cast its shadows in are gone with it.
    expect(css).not.toMatch(/--shadow-ink/)
  })

  it('draws the 16px grid on the body, one shade lighter than the site draws it', () => {
    // The site draws the same tile at 7% (projects/website/src/system.css); an
    // application page takes 4%, so it is the same paper without fighting a dense
    // screen. The soft system had dropped the grid entirely.
    expect(declaration(':root', '--grid-cell')).toBe('16px')
    expect(declaration(':root', '--grid-ink')).toBe(
      'color-mix(in oklab, var(--color-prussian-blue) 4%, transparent)',
    )
    const base = block('body')
    expect(base).toContain('background-size: var(--grid-cell) var(--grid-cell)')
    expect(base).toMatch(/linear-gradient\(to right, var\(--grid-ink\) 1px, transparent 1px\)/)
    expect(base).toMatch(/linear-gradient\(to bottom, var\(--grid-ink\) 1px, transparent 1px\)/)
  })

  it('points the sidebar at the Prussian Blue menu, with a Watermelon Strong tile', () => {
    const root = block(':root')
    expect(root).toMatch(/--sidebar:\s*var\(--color-prussian-blue\)/)
    expect(root).toMatch(/--sidebar-foreground:\s*var\(--color-paper\)/)
    expect(root).toMatch(/--sidebar-primary:\s*var\(--color-watermelon-strong\)/)
    expect(root).toMatch(/--sidebar-accent:\s*color-mix\(in oklab, #ffffff 10%, var\(--color-prussian-blue\)\)/)
    // The label on an active or hovered item, which has to stay legible on that tile.
    expect(root).toMatch(/--sidebar-accent-foreground:\s*#ffffff/)
    expect(root).toMatch(/--sidebar-border:\s*color-mix\(in oklab, #ffffff 12%, var\(--color-prussian-blue\)\)/)
    // Paper, not --ring: Watermelon at 50% over Prussian Blue is 1.73:1 on the panel.
    expect(root).toMatch(/--sidebar-ring:\s*var\(--color-paper\)/)
  })

  it('builds both quiet fills out of the palette, never a hand-written hex', () => {
    // `--muted` is the table's hover and every quiet fill, and it *is* Paper, since
    // that is what the spec's «hover Paper» means and what `ui/table.tsx` draws as
    // `hover:bg-muted`. `--secondary` is the secondary button and badge, one step of
    // the ink above the card. Both were hand-mixed hexes with a green-cyan cast
    // (#eef4f2, #e6ecea) until 2026-09-08, belonging to no tint in the palette: a
    // tint, or a mix of tints toward white, is all either may be.
    expect(declaration(':root', '--muted')).toBe('var(--color-paper)')
    const secondary = declaration(':root', '--secondary')
    expect(secondary).toMatch(/^(?:var\(--color-[a-z-]+\)|color-mix\(in oklab,)/)
    for (const hex of secondary.match(/#[0-9a-fA-F]{3,8}/g) ?? []) {
      expect(hex.toLowerCase(), 'hex in --secondary').toBe('#ffffff')
    }
    for (const [, name] of secondary.matchAll(/var\(--([a-z0-9-]+)\)/g)) {
      expect(name, 'var in --secondary').toMatch(/^color-/)
    }
  })

  it('hands the viewport height down to #root', () => {
    // Both shells size themselves with `h-full`: a browser that mis-reports 100dvh (an
    // embedded webview behind a toolbar) otherwise grows the page and clips the
    // sidebar's profile block.
    expect(css).toMatch(/html,\s*body,\s*#root\s*\{[^}]*height:\s*100%/)
  })
})

describe('one colour scheme', () => {
  it('declares no dark block anywhere', () => {
    // Dropped on 2026-09-18 with the same letter that chose these shapes: nothing set
    // the class, and a second palette would have to be kept honest against every rule
    // above for no reader. It returns through its own brief, never as a block of
    // overrides kept beside the light one.
    expect(tokensCss).not.toMatch(/^\s*\.dark\b/m)
  })

  it('still binds dark: to that class, so no OS preference reaches a utility', () => {
    // Tailwind v4 defaults `dark:` to prefers-color-scheme. The primitives in this
    // package carry no `dark:` utility since REB-300, but the five generated components
    // that stayed in the CRM still do (`dark:bg-input/30` on a field), and bound to the
    // OS they would fill a visitor's fields with ink at 30% over the paper, which is the
    // bug ORB-138 fixed in the hub. Bound to a class nobody adds, they stay inert.
    expect(tokensCss).toMatch(/@custom-variant dark \(&:is\(\.dark \*\)\);/)
  })
})

describe('the chart hues', () => {
  const CHART_TOKENS = ['--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5']

  it('declares five of them', () => {
    for (const token of CHART_TOKENS) {
      expect(css).toContain(`${token}:`)
    }
  })

  it('builds every one out of existing tints, with no raw hex but white', () => {
    for (const token of CHART_TOKENS) {
      const value = css.match(new RegExp(`${token}:\\s*([^;]+);`))![1]!
      expect(value).toContain('color-mix(')
      expect(value).toContain('var(--color-')
      const hexes = value.match(/#[0-9a-fA-F]{3,8}/g) ?? []
      expect(hexes.every((hex) => hex.toLowerCase() === '#ffffff')).toBe(true)
    }
  })

  it('declares them outside every @theme block', () => {
    // A @theme entry holding a color-mix() of a var() cannot be resolved into Tailwind
    // utilities at build time, which is what @theme's literal hexes are for. These are
    // read as var(--chart-n) in inline styles only. Both blocks are checked: a token
    // misfiled in `@theme inline` would be just as unresolvable.
    const blocks = [...css.matchAll(/@theme[^{]*\{([^}]*)\}/g)].map((match) => match[1]!)
    expect(blocks.length).toBeGreaterThanOrEqual(2)
    for (const themeBlock of blocks) {
      for (const token of CHART_TOKENS) {
        expect(themeBlock).not.toContain(token)
      }
    }
  })

  /**
   * The sRGB each token resolves to, derived by the evaluator above from the
   * declarations themselves rather than typed in from a browser, so an edit to an
   * expression fails here instead of quietly invalidating the contrast checks below.
   */
  const CHART_HEX: Record<string, string> = {
    '--chart-1': '#f86774',
    '--chart-2': '#3f526a',
    '--chart-3': '#d6c265',
    '--chart-4': '#616c79',
    '--chart-5': '#5f2e44',
  }

  it('resolves each token to the sRGB value the contrast checks below assume', () => {
    for (const [token, hex] of Object.entries(CHART_HEX)) {
      expect(evaluateChartToken(token), token).toBe(hex)
    }
  })

  it('keeps every chart colour a real mark on the ground it is drawn on', () => {
    // Read this with the WARN it records. 3:1, WCAG's threshold for a graphical object
    // rather than for body text, is what a mark carrying meaning must clear, and these
    // five do not clear it against both grounds. It is legal here, and only here,
    // because colour encodes nothing in these shapes: every bar sits in its own row
    // with its label and its value as text, and every chart renders an equivalent
    // table as the accessible rendering. Introduce a shape where colour is the only
    // thing telling two series apart and this is no longer the right gate: raise it to
    // 3:1 on both grounds and re-step the tokens rather than relaxing the shape.
    const light = tokenHex('paper')
    const ink = tokenHex('prussian-blue')
    for (const [token, hex] of Object.entries(CHART_HEX)) {
      const onLight = contrastRatio(hex, light)
      const onInk = contrastRatio(hex, ink)
      expect(Math.max(onLight, onInk), `${token} on its better surface`).toBeGreaterThanOrEqual(3)
      // 1.5:1 is the floor below which a fill stops reading as a shape at all. The
      // measured worst is --chart-5 against the ink at 1.64:1; this is the ratchet that
      // keeps a future edit from spending that headroom.
      expect(Math.min(onLight, onInk), `${token} on its worse surface`).toBeGreaterThanOrEqual(1.5)
    }
  })

  it('separates every pair by perceptual distance, not only by lightness', () => {
    // A contrast ratio is a lightness comparison: it scores two colours of the same
    // luminance and different hue as identical. Oklab deltaE is the measure that does
    // not. The worst pair here is --chart-2 against --chart-4 at 9.6, two slates one
    // derivation apart, below the 15 a palette carrying identity would need and
    // acceptable for the same reason the WARN above stands. 9 is the ratchet.
    const computed = CHART_TOKENS.map((token) => CHART_HEX[token]!)
    for (let i = 0; i < computed.length; i += 1) {
      for (let j = i + 1; j < computed.length; j += 1) {
        expect(
          deltaE(computed[i]!, computed[j]!),
          `${CHART_TOKENS[i]} vs ${CHART_TOKENS[j]}`,
        ).toBeGreaterThanOrEqual(9)
        expect(contrastRatio(computed[i]!, computed[j]!)).toBeGreaterThanOrEqual(1.2)
      }
    }
  })
})
