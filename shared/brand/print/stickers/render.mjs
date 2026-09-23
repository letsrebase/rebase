/**
 * Renders stickers.html into out/: for every `.sticker` a PNG at 300 dpi with the bleed
 * (png/<name>-with-bleed.png), one at the trim (png/<name>.png) and one PDF
 * (pdf/<name>.pdf) at the bleed size, vector text, which is what the print shop takes.
 *
 *   node shared/brand/print/stickers/render.mjs [--guides]
 *
 * `--guides` draws the trim and the safe area as dashed lines and writes to out/guide/
 * instead. Playwright is @rebase/brand's own devDependency. The exact-size crop uses
 * `sips`, so this runs on macOS.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))
const page_path = join(here, 'stickers.html')
const DPI = 300, dpr = DPI / 96, MM = 96 / 25.4, BLEED = 3
const guides = process.argv.includes('--guides')
const out = join(here, 'out', guides ? 'guide' : 'png')
mkdirSync(out, { recursive: true })
if (!guides) mkdirSync(join(here, 'out', 'pdf'), { recursive: true })
const pxOf = (mm) => Math.round((mm / 25.4) * DPI)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 600, height: 900 }, deviceScaleFactor: dpr })
await page.goto(`${pathToFileURL(page_path)}${guides ? '?guides=1' : ''}`)
await page.evaluate(() => document.fonts.ready)
await page.waitForTimeout(400)

const items = await page.locator('.sticker').evaluateAll((els) => els.map((e) => ({ name: e.dataset.name, trim: e.dataset.trim })))
for (const [i, it] of items.entries()) {
  const [tw, th] = it.trim.split('x').map(Number)
  const bb = await page.locator('.sticker').nth(i).boundingBox()
  const x = Math.round(bb.x * dpr) / dpr, y = Math.round(bb.y * dpr) / dpr
  // Playwright floors a fractional clip: ask for a pixel and a half more, then cut
  // every bitmap to its exact size with sips.
  const over = 1.5 / dpr
  await page.screenshot({ path: join(out, `${it.name}-with-bleed.png`), fullPage: true, clip: { x, y, width: (tw + 2 * BLEED) * MM + over, height: (th + 2 * BLEED) * MM + over } })
  await page.screenshot({ path: join(out, `${it.name}.png`), fullPage: true, clip: { x: x + BLEED * MM, y: y + BLEED * MM, width: tw * MM + over, height: th * MM + over } })
  execFileSync('sips', ['--cropToHeightWidth', String(pxOf(th + 2 * BLEED)), String(pxOf(tw + 2 * BLEED)), join(out, `${it.name}-with-bleed.png`)], { stdio: 'ignore' })
  execFileSync('sips', ['--cropToHeightWidth', String(pxOf(th)), String(pxOf(tw)), join(out, `${it.name}.png`)], { stdio: 'ignore' })
}
if (guides) { await browser.close(); console.log('guides'); process.exit(0) }

// One PDF per sticker, each on a page of its own bleed size: ?only=<name> hides the others.
for (const it of items) {
  const [tw, th] = it.trim.split('x').map(Number)
  const p = await browser.newPage({ viewport: { width: 600, height: 900 } })
  await p.goto(`${pathToFileURL(page_path)}?only=${it.name}`)
  await p.evaluate(() => document.fonts.ready)
  await p.waitForTimeout(300)
  await p.addStyleTag({ content: `@page { size: ${tw + 2 * BLEED}mm ${th + 2 * BLEED}mm; margin: 0; } @media print { .sticker { margin: 0; } }` })
  await p.emulateMedia({ media: 'print' })
  await p.pdf({ path: join(here, 'out', 'pdf', `${it.name}.pdf`), width: `${tw + 2 * BLEED}mm`, height: `${th + 2 * BLEED}mm`, printBackground: true, preferCSSPageSize: true, margin: { top: 0, right: 0, bottom: 0, left: 0 } })
  await p.close()
}
await browser.close()
console.log('ok')
