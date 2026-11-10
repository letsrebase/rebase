import { BRAND_TILES, BRAND_TILE_CLASSES } from '@rebase/brand/mark'
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BrandMark } from './brand-mark'

describe('BrandMark', () => {
  it('draws the brand tiles in the brand order, with the brand classes', () => {
    const { container } = render(<BrandMark />)
    const tiles = Array.from(container.querySelectorAll('span > span'))
    expect(tiles.map((tile) => tile.className)).toEqual(
      BRAND_TILES.map((tile) => BRAND_TILE_CLASSES[tile]),
    )
  })

  it('is hidden from assistive technology and takes a size from the caller', () => {
    const { container } = render(<BrandMark className="size-5" />)
    const mark = container.firstElementChild
    expect(mark?.getAttribute('aria-hidden')).toBe('true')
    expect(mark?.classList.contains('size-5')).toBe(true)
  })
})
