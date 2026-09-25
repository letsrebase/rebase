import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BRAND_TILES, BRAND_TILE_CLASSES } from '@rebase/brand/mark'

import { Loader } from './loader'

/** `rebase-loader-replay`'s own duration, `tokens.css`. */
const CYCLE_MS = 800

/**
 * The peak moment of a tile carrying `animation-delay: -Xms` on a keyframe that peaks
 * at 0%/100%: the tile is X ms into its local clock at t=0, so its next peak is
 * `CYCLE_MS - X` ms away. A `0ms` delay (no minus sign) is its own case, since
 * `style.animationDelay` reports it as `"0ms"` rather than `"-0ms"`.
 */
function peakMsFromDelay(delay: string): number {
  const ms = Number.parseFloat(delay)
  return ms === 0 ? 0 : (CYCLE_MS + ms) % CYCLE_MS
}

/**
 * The loading state is the brand's own mark, animated, not a fifth shape: same tile
 * order and colours as `BrandMark` (BrandMark.test.tsx in each application asserts
 * the static version against the same constants), plus the two things unique to a
 * loader -- it must go inert under `prefers-reduced-motion`, and each tile must peak
 * in the mark's own reading order rather than together.
 *
 * The peak-order test asserts the *effect* of the delay (via `peakMsFromDelay`)
 * rather than copying the delay strings the component happens to use: a hardcoded
 * copy of the literals would still have passed the 2026-09-25 regression, where the
 * second and fourth tile's delays were swapped and their peaks landed in the wrong
 * order while the four strings were each individually "a delay".
 */
describe('Loader', () => {
  it('draws the four brand tiles in reading order', () => {
    const { container } = render(<Loader />)
    const tiles = [...container.querySelectorAll('span > span')]
    expect(tiles.map((tile) => tile.className.split(' ')).map((classes) =>
      classes.find((c) => Object.values(BRAND_TILE_CLASSES).includes(c)),
    )).toEqual(BRAND_TILES.map((tile) => BRAND_TILE_CLASSES[tile]))
  })

  it('carries a data-slot, the same convention every other primitive in this package follows', () => {
    const { container } = render(<Loader />)
    expect(container.firstElementChild).toHaveAttribute('data-slot', 'loader')
  })

  it('is decorative, so assistive technology never announces it', () => {
    const { container } = render(<Loader />)
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true')
  })

  it("peaks each tile 200ms after the previous one, in the mark's own reading order", () => {
    const { container } = render(<Loader />)
    const tiles = [...container.querySelectorAll('span > span')] as HTMLElement[]
    const peaks = tiles.map((tile) => peakMsFromDelay(tile.style.animationDelay))
    expect(peaks).toEqual([0, 200, 400, 600])
  })

  it('goes inert under prefers-reduced-motion, at full strength', () => {
    const { container } = render(<Loader />)
    const tiles = [...container.querySelectorAll('span > span')]
    for (const tile of tiles) {
      expect(tile.className).toContain('motion-reduce:animate-none')
      expect(tile.className).toContain('motion-reduce:opacity-100')
    }
  })

  it('accepts a size override the same way BrandMark does', () => {
    const { container } = render(<Loader className="size-12" />)
    expect(container.firstElementChild).toHaveClass('size-12')
    expect(container.firstElementChild).not.toHaveClass('size-3')
  })
})
