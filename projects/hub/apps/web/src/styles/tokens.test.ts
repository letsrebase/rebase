import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * What is left of this file after REB-299: the hub's own `.site` scope, and nothing
 * the two applications share. Every semantic slot, the radius scale, the shadow
 * indirections and the palette are `@rebase/ui`'s, and its `tokens.test.ts` is the
 * contract for them.
 *
 * The scope itself is ORB-73 (`docs/design/DECISIONS.md`, 2026-09-10): the chooser,
 * the two wizards and the thanks page are the landing continued and keep its own
 * weights, a 2px line and an 8px step, where the application draws 1px and 4px since
 * 2026-09-18. Squared corners and the ink colours are no longer this scope's job:
 * they are the default everywhere, which is what that record changed.
 */
const tokensCss = readFileSync(join(__dirname, 'tokens.css'), 'utf-8')
const brandCss = readFileSync(
  fileURLToPath(import.meta.resolve('@rebase/brand/palette.css')),
  'utf-8',
)
const css = `${brandCss}\n${tokensCss}`

/** Every declaration of a selector in tokens.css, concatenated in file order. A
 *  selector like `.site` can legitimately appear more than once (ORB-74 adds a
 *  second `.site` rule for the page ground rather than editing ORB-73's), and a
 *  non-global match would silently see only the first one, exempting every later
 *  occurrence from the assertions below, including the raw-hex guard. */
function block(selector: string): string {
  const escaped = selector.replace(/[.[\]*+?^${}()|\\]/g, '\\$&')
  const bodies = [
    ...css.matchAll(new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([\\s\\S]*?)\\n\\s*\\}`, 'g')),
  ].map((match) => match[1]!)
  if (bodies.length === 0) throw new Error(`rule "${selector}" not found in tokens.css`)
  return bodies.join('\n')
}

const site = block('.site')

/** The value one token is assigned inside one rule, by exact selector. */
function declaration(selector: string, token: string): string {
  const value = block(selector).match(new RegExp(`${token}:\\s*([^;]+);`))?.[1]
  if (!value) throw new Error(`${token} is not declared in "${selector}"`)
  return value.trim()
}

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
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/** Resolves a `--landing-*` colour declared in `.site` to the sRGB hex a browser
 *  would compute, by following its `var(--color-…)` back into the shared palette both
 *  this file and `landing.css` import. A hand-mixed hex here would be a second
 *  palette; this is what keeps that mechanically impossible rather than discouraged. */
function siteColourHex(token: string): string {
  const reference = declaration('.site', token).match(/^var\(--color-([a-z-]+)\)$/)
  if (!reference) throw new Error(`${token} in .site is not a palette reference`)
  const hex = css.match(new RegExp(`--color-${reference[1]}:\\s*(#[0-9a-fA-F]{6})`))?.[1]
  if (!hex) throw new Error(`palette has no --color-${reference[1]}`)
  return hex
}

describe('the .site scope (ORB-73)', () => {
  it('resolves every --landing-* colour to the same hex the site itself pins', () => {
    // The site's own test (landing-tokens.test.ts) asserts these five against the
    // same shared palette; a value that drifted from either side would fail here too.
    expect(siteColourHex('--landing-surface')).toBe('#f1f2f3')
    expect(siteColourHex('--landing-ink')).toBe('#011936')
    expect(siteColourHex('--landing-ink-quiet')).toBe('#465362')
    expect(siteColourHex('--landing-cta')).toBe('#e5133e')
    expect(siteColourHex('--landing-focus')).toBe('#ed254e')
  })

  it('carries the CTA at 4.5:1 for its own ink, the same pair the application relies on', () => {
    expect(
      contrastRatio(declaration('.site', '--landing-cta-ink'), siteColourHex('--landing-cta')),
    ).toBeGreaterThanOrEqual(4.5)
  })

  it('contains no raw hexadecimal in the .site block, other than white', () => {
    for (const hex of site.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []) {
      expect(hex.toLowerCase(), `raw hex in .site: ${hex}`).toBe('#ffffff')
    }
  })

  /** Splits a `box-shadow` value on whitespace outside of any parentheses, so a
   *  `calc(a * b)` or `var(--x)` component is never cut at its own inner paren. */
  function shadowParts(value: string): string[] {
    const parts: string[] = []
    let depth = 0
    let current = ''
    for (const char of value) {
      if (char === '(') depth += 1
      if (char === ')') depth -= 1
      if (char === ' ' && depth === 0) {
        if (current) parts.push(current)
        current = ''
      } else {
        current += char
      }
    }
    if (current) parts.push(current)
    return parts
  }

  const SHADOWS = ['xs', 'sm', 'md', 'lg']

  it('casts every shadow as an ink offset, never a blur or a tint', () => {
    for (const step of SHADOWS) {
      const parts = shadowParts(declaration('.site', `--shadow-app-${step}`))
      // <offset-x> <offset-y> 0 var(--landing-ink): the third length is the blur
      // radius, held at exactly 0 (landing.css:31, "an offset, never a blur"), and the
      // colour is the opaque ink.
      expect(parts, `--shadow-app-${step}`).toHaveLength(4)
      expect(parts[0], `--shadow-app-${step} offset-x`).toMatch(/^(?:calc\(.*\)|var\(.*\)|[\d.]+px)$/)
      expect(parts[1], `--shadow-app-${step} offset-y`).toMatch(/^(?:calc\(.*\)|var\(.*\)|[\d.]+px)$/)
      expect(parts[2], `--shadow-app-${step} blur`).toBe('0')
      expect(parts[3], `--shadow-app-${step} colour`).toBe('var(--landing-ink)')
    }
  })

  it('keeps the site at its own weights, twice the application it sits inside', () => {
    // landing.css's own two magnitudes: 8px for .box, 6px for .card, the smaller one
    // three quarters of the larger (landing.css:31, landing.css:158), and a 2px line
    // where the application draws 1px. This is the whole difference between a public
    // page of the hub and its admin area, now that squared is the shared default.
    expect(declaration('.site', '--landing-step')).toBe('8px')
    expect(declaration('.site', '--landing-border-width')).toBe('2px')
    expect(declaration('.site', '--shadow-app-xs')).toBe(declaration('.site', '--shadow-app-sm'))
    expect(declaration('.site', '--shadow-app-md')).toBe(declaration('.site', '--shadow-app-lg'))
    expect(shadowParts(declaration('.site', '--shadow-app-md'))[0]).toBe('var(--landing-step)')
    expect(shadowParts(declaration('.site', '--shadow-app-xs'))[0]).toBe(
      'calc(var(--landing-step) * 0.75)',
    )
  })

  it('declares nothing the shared tokens already decide', () => {
    // Radius, the ink colours and the semantic slots are `@rebase/ui`'s since REB-299:
    // a `--radius` or a `--border` restated here would be a second source for a value
    // the record settled for both applications.
    expect(site).not.toMatch(/--radius:/)
    expect(site).not.toMatch(/--border:/)
    expect(site).not.toMatch(/--input:/)
  })

  it('draws the site ground at 7%, over the 4% the application draws', () => {
    expect(declaration('.site', '--landing-grid')).toBe(
      'color-mix(in oklab, var(--landing-ink) 7%, transparent)',
    )
    expect(declaration('.site', '--landing-cell')).toBe('16px')
  })
})

describe('the dark variant (ORB-138)', () => {
  const webRoot = join(__dirname, '..', '..')
  /** Every source that can put a class on an element: the app's own TypeScript and
   *  the HTML shell it mounts into. Tests are included on purpose, since a `.dark`
   *  set in a test would be pinning behaviour the app does not have; this file is the
   *  one exception, since its own test names say the word. */
  const sources = [
    ...readdirSync(join(webRoot, 'src'), { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile() && /\.tsx?$/.test(entry.name) && entry.name !== 'tokens.test.ts')
      .map((entry) => join(entry.parentPath, entry.name)),
    join(webRoot, 'index.html'),
  ].map((path) => ({ path, text: readFileSync(path, 'utf-8') }))

  it('names dark nowhere at all, in any form', () => {
    // Until REB-300 this app had primitives of its own and they carried `dark:`
    // utilities, so the check here was that none of them, and no rule in this
    // stylesheet, ever named the bare class: that is the day a half-designed second
    // theme starts. The primitives moved into `@rebase/ui`, which strips those
    // utilities and pins the variant binding in its own test, so what is left to
    // prove here is stronger and simpler: this application does not mention dark in
    // any form, prefix or class.
    expect(sources.length).toBeGreaterThan(20)
    for (const { path, text } of sources) {
      expect(text, path).not.toMatch(/\bdark:/)
      expect(text, path).not.toMatch(/(["'`])[^"'`\n]*\bdark\b(?!:)[^"'`\n]*\1/)
    }
    expect(tokensCss).not.toMatch(/^\s*\.dark\b/m)
  })
})
