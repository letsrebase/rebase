/**
 * Draws the icons an installed PigroCRM wears: the four-tile mark on the Paper ground,
 * written into `shared/brand/pwa/` (REB-731).
 *
 * Not part of any build, and run by hand: `pnpm --filter @rebase/brand build:pwa-icons`.
 * The PNGs are committed, so the CRM's build never waits on a browser and the file that
 * ships is the file somebody looked at. A directory given as the first argument receives
 * the files instead, so what this draws today can be compared with what is committed.
 *
 * Three kinds, because three platforms read an icon differently:
 *
 * - `icon-192.png`, `icon-512.png` (`purpose: any`): the mark at 64% of the square, for
 *   a launcher or a task switcher that shows the picture as it is.
 * - `icon-maskable-192.png`, `icon-maskable-512.png` (`purpose: maskable`): the same
 *   mark at 52%, so it stays inside the safe zone, the circle of 40% of the side that
 *   every Android launcher mask leaves visible. The ground runs to the edge because the
 *   launcher crops it to its own shape.
 * - `apple-touch-icon.png`, 180px: iOS ignores the manifest's icons and rounds the
 *   corners itself, so this is the any-purpose drawing at the size it asks for, with no
 *   transparency (iOS fills it black).
 *
 * The order of the tiles comes out of `mark.ts` and the colours out of `palette.css`,
 * never restated here (`ink` is Prussian Blue, as `mark.ts` says).
 */
import { mkdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'
import { BRAND_TILES } from '../mark.ts'

const here = dirname(fileURLToPath(import.meta.url))
const brand = join(here, '..')
const out = process.argv[2] ?? join(brand, 'pwa')

const palette = readFileSync(join(brand, 'palette.css'), 'utf-8')
function colour(name) {
  const hex = new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(palette)?.[1]
  if (!hex) throw new Error(`--color-${name} is not declared in palette.css`)
  return hex
}
const paper = colour('paper')
const TILE_COLOUR = {
  ink: colour('prussian-blue'),
  'royal-gold': colour('royal-gold'),
  watermelon: colour('watermelon'),
}
// The mark is a 2x2 field, read left to right and top to bottom.
const tiles = BRAND_TILES.map(
  (tile, i) =>
    `<rect x="${i % 2}" y="${Math.floor(i / 2)}" width="1" height="1" fill="${TILE_COLOUR[tile]}"/>`,
).join('')

const ICONS = [
  { file: 'icon-192.png', size: 192, share: 0.64 },
  { file: 'icon-512.png', size: 512, share: 0.64 },
  { file: 'icon-maskable-192.png', size: 192, share: 0.52 },
  { file: 'icon-maskable-512.png', size: 512, share: 0.52 },
  { file: 'apple-touch-icon.png', size: 180, share: 0.64 },
]

function markup({ size, share }) {
  // An even number of pixels, so each of the two tiles across is whole and no edge blurs.
  const side = Math.round((size * share) / 2) * 2
  return `<!doctype html><meta charset="utf-8"><style>
    html, body { margin: 0; background: ${paper}; }
    #icon { width: ${size}px; height: ${size}px; display: grid; place-items: center; background: ${paper}; }
    svg { width: ${side}px; height: ${side}px; display: block; }
  </style><div id="icon"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2" shape-rendering="crispEdges">${tiles}</svg></div>`
}

mkdirSync(out, { recursive: true })
const browser = await chromium.launch()
try {
  for (const icon of ICONS) {
    const page = await browser.newPage({ viewport: { width: icon.size, height: icon.size } })
    await page.setContent(markup(icon), { waitUntil: 'load' })
    await page.locator('#icon').screenshot({ path: join(out, icon.file) })
    await page.close()
    console.log(`${icon.file}: ${icon.size}px -> ${out}`)
  }
} finally {
  await browser.close()
}
