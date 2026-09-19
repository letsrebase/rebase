/**
 * The colour maths every surface's test measures its pairs with, and the palette
 * reader they all resolve tokens through.
 *
 * It lives beside the palette because that is what it is about: a contrast ratio is a
 * property of two colours, and the colours are here. Until REB-301 the same WCAG
 * formula was written three times, in `projects/website/src/landing-tokens.test.ts`,
 * `projects/hub/apps/web/src/styles/tokens.test.ts` and `shared/ui/tokens.test.ts`,
 * each with its own rounding of the sRGB transfer function: two used 0.03928 as the
 * knee and one 0.04045, which is the difference between the standard and a typo that
 * has been copied around the web for twenty years. It never changed a verdict at these
 * colours, and that is exactly why nobody would have noticed it start to.
 *
 * What is deliberately NOT here: resolving a stylesheet. Walking selectors, working
 * out which rule wins on an element and compositing an ancestor chain is the site's
 * own problem (`landing-tokens.test.ts` does it over plain CSS it owns), and for an
 * application the answer comes from a real browser instead, which is what
 * `shared/ui/e2e/gallery.spec.ts` asks Chromium for. Two implementations of the
 * cascade would be worse than none.
 */

export type Rgb = [number, number, number]

/** WCAG's own thresholds, named so a test says which one it is asserting. */
export const AA_TEXT = 4.5
export const AA_LARGE_TEXT = 3
/** A border, an icon, a focus ring: WCAG 2.1 SC 1.4.11, non-text contrast. */
export const AA_NON_TEXT = 3

export function hexToRgb(hex: string): Rgb {
  const value = hex.replace('#', '')
  const full =
    value.length === 3
      ? `${value[0]}${value[0]}${value[1]}${value[1]}${value[2]}${value[2]}`
      : value
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ]
}

/** sRGB to linear light, with the 0.04045 knee of the specification. */
function toLinear(channel: number): number {
  const c = channel / 255
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

export function relativeLuminance([r, g, b]: Rgb): number {
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b)
}

/** WCAG 2.x contrast ratio, order-independent. */
export function contrastRatio(hexA: string, hexB: string): number {
  const a = relativeLuminance(hexToRgb(hexA))
  const b = relativeLuminance(hexToRgb(hexB))
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/**
 * `hexA` at `weightAPercent` over `hexB`, composited in sRGB. A browser mixing in
 * OKLab lands on a slightly different colour, so this is not a substitute for what
 * Chromium computes: it is close enough at the percentages the products actually use
 * to put a ratio on the right side of a threshold, and a test that needs the exact
 * value asks a browser for it.
 */
export function blendSrgb(hexA: string, hexB: string, weightAPercent: number): string {
  const a = hexToRgb(hexA)
  const b = hexToRgb(hexB)
  const w = weightAPercent / 100
  const channel = (i: 0 | 1 | 2) => Math.round(a[i]! * w + b[i]! * (1 - w))
  return `#${[channel(0), channel(1), channel(2)]
    .map((c) => c.toString(16).padStart(2, '0'))
    .join('')}`
}

/**
 * Every `--color-*` the brand palette declares, as a name-to-hex map. Read from the
 * stylesheet rather than restated in TypeScript: a second list of the colours is a
 * second palette, and it would be right until the day somebody changes one.
 */
export function paletteFrom(css: string): Record<string, string> {
  const palette: Record<string, string> = {}
  for (const declaration of css.matchAll(/(--color-[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    const [, name, hex] = declaration
    if (name && hex) palette[name] = hex.toLowerCase()
  }
  return palette
}
