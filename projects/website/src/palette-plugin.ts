import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'

const TOKENS_CSS = fileURLToPath(import.meta.resolve('@rebase/brand/palette.css'))
/** The rules the landing sheets and the pitch deck share (grid line, tile, glyph,
 *  contrast guard), prepended after the tokens so no consumer restates them. */
const SYSTEM_CSS = resolve(__dirname, 'system.css')
/** The typeface, self-hosted, shared with the application: one @font-face for the
 *  whole brand rather than a copy in each sheet. Prepended rather than @import-ed
 *  because an @import is only valid before any rule, and both sheets open with
 *  `:root`. */
const FONT_CSS = fileURLToPath(import.meta.resolve('@rebase/brand/font.css'))

/** The palette and the font stack, and nothing else. Eight today, the newest colour
 *  being the deep watermelon REB-307 added, third in declaration order; the count is
 *  asserted so that a token added to or removed from palette.css is a failing test
 *  rather than a silently thinner landing. */
const EXPECTED_TOKEN_COUNT = 8

/**
 * Reads the custom properties `palette.css` declares inside a `@theme` block and
 * that the landing can meaningfully use on its own.
 *
 * Why extraction and not an `@import`: the palette lives inside Tailwind v4's
 * `@theme` at-rule, whose literal hex values are what let the app generate
 * `bg-watermelon/50`-style utilities. Pointing `@theme` at `var()` indirections to
 * make the block shareable would break that. The landing has no Tailwind at all, so
 * it cannot consume `@theme` either way. Copying the seven hexes into `landing.css`
 * is the obvious alternative and is exactly the fork this function exists to make
 * impossible: there is one source of colour, and a landing built from a stale copy
 * of it cannot happen because no copy exists.
 *
 * The `var()` filter is what keeps `@theme inline`'s 26 semantic re-exports out.
 * They point at `--primary`, `--background` and friends, which live in
 * `palette.css`'s `:root` and are the *app's* theme, not the shared system.
 */
export function extractSharedTokens(css: string): Record<string, string> {
  const collected: Record<string, string> = {}
  // palette.css's two `@theme` blocks contain no nested braces, so a non-greedy
  // match up to the first `}` is exact here.
  for (const block of css.matchAll(/@theme[^{]*\{([^}]*)\}/g)) {
    for (const decl of (block[1] ?? '').matchAll(
      /(--(?:color|radius|font)[\w-]*)\s*:\s*([^;]+);/g,
    )) {
      const name = decl[1]
      const value = decl[2]
      if (name && value) collected[name] = value.trim()
    }
  }

  const names = new Set(Object.keys(collected))
  const shared: Record<string, string> = {}
  for (const [name, value] of Object.entries(collected)) {
    const references = [...value.matchAll(/var\((--[\w-]+)\)/g)].map((match) => match[1])
    if (references.every((reference) => reference !== undefined && names.has(reference))) {
      shared[name] = value
    }
  }

  const count = Object.keys(shared).length
  if (count !== EXPECTED_TOKEN_COUNT) {
    throw new Error(
      `extractSharedTokens extracted only ${count} shared token${count === 1 ? '' : 's'} ` +
        `from palette.css (expected ${EXPECTED_TOKEN_COUNT}). The site must not restate ` +
        'the palette: fix the extraction, do not paste values into landing.css.',
    )
  }
  return shared
}

/** The stylesheets that receive the tokens and the shared system: the landing's own,
 *  the community page's, and the pitch deck's. */
const TOKEN_CONSUMERS = ['src/landing.css', 'src/community.css', 'src/pitch.css']

/** Prepends the shared tokens, then system.css, to each stylesheet in TOKEN_CONSUMERS,
 *  at build and at dev time. */
export function palettePlugin(): Plugin {
  return {
    name: 'website-palette',
    enforce: 'pre',
    transform(code, id) {
      const file = id.split('?')[0] ?? ''
      if (!TOKEN_CONSUMERS.some((consumer) => file.endsWith(consumer))) return null
      const tokens = extractSharedTokens(readFileSync(TOKENS_CSS, 'utf-8'))
      const block = Object.entries(tokens)
        .map(([name, value]) => `  ${name}: ${value};`)
        .join('\n')
      const system = readFileSync(SYSTEM_CSS, 'utf-8')
      const font = readFileSync(FONT_CSS, 'utf-8').replace(
        "url('./fonts/", `url('${resolve(FONT_CSS, '../fonts')}/`,
      )
      return (
        `/* injected from @rebase/brand/font.css by palette-plugin.ts */\n${font}\n\n` +
        `/* injected from @rebase/brand/palette.css by palette-plugin.ts */\n:root {\n${block}\n}\n\n` +
        `/* injected from src/system.css by palette-plugin.ts */\n${system}\n${code}`
      )
    },
  }
}
