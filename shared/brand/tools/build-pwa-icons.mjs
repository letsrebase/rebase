/**
 * Draws the icons an installed PigroCRM wears: the four tiles of the mark edge to edge, with
 * no ground around them, written into `shared/brand/pwa/` (REB-731, REB-951, REB-1109).
 *
 * Not part of any build, and run by hand: `pnpm --filter @rebase/brand build:pwa-icons`.
 * The PNGs are committed, so the CRM's build never waits on a browser and the file that
 * ships is the file somebody looked at. A directory given as the first argument receives
 * the files instead, so what this draws today can be compared with what is committed.
 *
 * One drawing, because the mark is a square of flat colour and every platform shows it best
 * filling the square it is given:
 *
 * - `icon-192.png`, `icon-512.png`: listed in the manifest twice, as `any` and as
 *   `maskable`. A launcher's mask (a circle on most Android phones) is cut out of the tiles
 *   themselves and reads as four coloured quarters; nothing needs the safe zone because
 *   whatever the mask crops, the rest is still the mark. A launcher or a desktop that shows
 *   the `any` picture as it is gets the same full square, never a smaller one on a ground.
 *   The 512 is also the Slack app's icon (the Slack runbook, step 1).
 * - `apple-touch-icon.png`, 180px: iOS ignores the manifest's icons and rounds the corners
 *   itself, so this is the same tiles at the size it asks for, with no transparency (iOS
 *   fills it black).
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

// Every size is even, so each of the two tiles across is whole and no edge blurs.
const ICONS = [
  { file: 'icon-192.png', size: 192 },
  { file: 'icon-512.png', size: 512 },
  { file: 'apple-touch-icon.png', size: 180 },
]

function markup({ size }) {
  return `<!doctype html><meta charset="utf-8"><style>
    html, body { margin: 0; }
    svg { width: ${size}px; height: ${size}px; display: block; }
  </style><svg id="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2" shape-rendering="crispEdges">${tiles}</svg>`
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
