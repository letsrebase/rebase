/**
 * Renders business-cards.html into out/: for each `.card` a PNG at 300 dpi with the
 * bleed (png/<name>-with-bleed.png, 61×90 mm → 720×1063 px) and one at the trim
 * (png/<name>.png, 55×84 mm → 650×992 px) for looking at it, then the two-page PDF
 * business-cards.pdf at 61×90 mm, vector text, which is what the print shop takes.
 *
 *   node shared/brand/print/business-cards/render.mjs [--guides] [--placeholders]
 *
 * `--guides` draws the trim and the safe area as dashed lines and writes to out/guide/
 * instead. Without contacts.local.js the back carries placeholders instead of the two
 * phone numbers, and the script stops before writing anything. `--placeholders` asks
 * for exactly that on purpose: the page ignores contacts.local.js, and everything goes
 * to out/preview/, never to the paths of the print file.
 *
 * Playwright is @rebase/brand's own devDependency. The exact-size crop uses `sips`,
 * so this runs on macOS.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))
const page_path = join(here, 'business-cards.html')
const guides = process.argv.includes('--guides')
const placeholders = process.argv.includes('--placeholders')
const params = [guides && 'guides=1', placeholders && 'placeholders=1'].filter(Boolean).join('&')
const root = join(here, 'out', placeholders ? 'preview' : '')

const DPI = 300, dpr = DPI / 96, MM = 96 / 25.4
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 400, height: 800 }, deviceScaleFactor: dpr })
await page.goto(`${pathToFileURL(page_path)}${params ? `?${params}` : ''}`)
await page.evaluate(() => document.fonts.ready)
await page.waitForTimeout(400)

if (!placeholders && (await page.evaluate(() => document.body.dataset.missingContacts === 'true'))) {
  await browser.close()
  console.error('contacts.local.js is missing or incomplete: copy contacts.example.js and write the two numbers, or pass --placeholders for a preview.')
  process.exit(1)
}

const out = join(root, guides ? 'guide' : 'png')
mkdirSync(out, { recursive: true })
const cards = page.locator('.card')
const names = await cards.evaluateAll((els) => els.map((e) => e.dataset.name))
// Clip to the target bitmap, not the rounded element box: 61×90 mm is 720×1063 px at
// 300 dpi, 55×84 mm is 650×992 px. Playwright floors a fractional clip, so the clip is
// given in device pixels converted back, a pixel and a half over, and every bitmap is
// then cut to its exact size.
const px = (n) => (n + 1.5) / dpr
for (const [i, name] of names.entries()) {
  const bb = await cards.nth(i).boundingBox()
  const b = { x: Math.round(bb.x * dpr) / dpr, y: Math.round(bb.y * dpr) / dpr }
  const bleed = join(out, `${name}-with-bleed.png`), trim = join(out, `${name}.png`)
  await page.screenshot({ path: bleed, clip: { x: b.x, y: b.y, width: px(720), height: px(1063) } })
  await page.screenshot({ path: trim, clip: { x: Math.round((b.x + 3 * MM) * dpr) / dpr, y: Math.round((b.y + 3 * MM) * dpr) / dpr, width: px(650), height: px(992) } })
  execFileSync('sips', ['--cropToHeightWidth', '1063', '720', bleed], { stdio: 'ignore' })
  execFileSync('sips', ['--cropToHeightWidth', '992', '650', trim], { stdio: 'ignore' })
}
if (guides) { await browser.close(); console.log(placeholders ? 'guides (preview)' : 'guides'); process.exit(0) }

await page.emulateMedia({ media: 'print' })
await page.pdf({
  path: join(root, placeholders ? 'business-cards-preview.pdf' : 'business-cards.pdf'),
  width: '61mm', height: '90mm',
  printBackground: true, preferCSSPageSize: true,
  margin: { top: 0, right: 0, bottom: 0, left: 0 },
})
await browser.close()
console.log(placeholders ? 'ok (preview, out/preview/)' : 'ok')
