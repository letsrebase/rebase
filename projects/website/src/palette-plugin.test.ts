import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { extractSharedTokens } from './palette-plugin'

const tokensCss = readFileSync(fileURLToPath(import.meta.resolve('@rebase/brand/palette.css')), 'utf-8')

describe('extractSharedTokens', () => {
  it('extracts exactly the eight tokens the brand shares: seven colours and the typeface', () => {
    expect(Object.keys(extractSharedTokens(tokensCss)).sort()).toEqual([
      '--color-charcoal-blue',
      '--color-paper',
      '--color-prussian-blue',
      '--color-royal-gold',
      '--color-watermelon',
      // The third step of the watermelon ramp (REB-307) arrives here because the
      // palette is extracted whole, not because the landing draws with it: the site's
      // call to action fills with `-strong` and reads white on it. A token the landing
      // receives and does not use costs one custom property; a token it needs and does
      // not receive is a dangling var(), which the guard below is about.
      '--color-watermelon-deep',
      '--color-watermelon-strong',
      '--font-sans',
    ])
  })

  it('carries the live hex, so an edit to the palette travels with it', () => {
    expect(extractSharedTokens(tokensCss)['--color-watermelon-strong']).toBe('#e5133e')
  })

  it('drops indirections rather than emitting dangling var() references', () => {
    // The guard that matters now that the palette is a package of its own: a token
    // whose value points at something the landing never receives would inject a
    // colour resolving to nothing. The application's `@theme inline` re-exports
    // (--color-primary: var(--primary) and 25 siblings) and its radius scale used to
    // arrive here for exactly that reason, and the landing used none of them.
    const extracted = extractSharedTokens(tokensCss)
    expect(extracted['--color-primary']).toBeUndefined()
    expect(extracted['--radius-2xl']).toBeUndefined()
    for (const value of Object.values(extracted)) {
      for (const [, referenced] of value.matchAll(/var\((--[\w-]+)\)/g)) {
        expect(Object.keys(extracted)).toContain(referenced)
      }
    }
  })

  it('refuses a stylesheet that has lost the palette, instead of emitting nothing', () => {
    expect(() => extractSharedTokens('@theme { --color-watermelon: #ed254e; }')).toThrow(
      /extracted only 1 shared token/,
    )
  })
})
