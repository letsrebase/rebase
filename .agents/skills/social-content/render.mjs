/**
 * Renders a social page (a carousel, a single square, a LinkedIn cover) built on
 * carousel-template.html or banner-template.html: one PNG per `.slide` and, for more
 * than one slide, one PDF with the same page size, which is what a LinkedIn document
 * post takes. Prints `overflow […]`: a frame whose content is taller than the box that
 * holds it is one LinkedIn crops, and it is not visible in a thumbnail.
 *
 *   node .claude/skills/social-content/render.mjs <page.html> [--size WxH]
 *
 * `--size` is for the covers (1584x396, 1128x191): it sets the viewport and passes
 * `?w=&h=` to the page. Playwright comes from `projects/website` (`@playwright/test`),
 * the one package here that depends on it; Chromium is the one its e2e tests install.
 */
import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { basename, dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../../../projects/website/package.json', import.meta.url))
const { chromium } = require('@playwright/test')

const [page_path, ...rest] = process.argv.slice(2)
if (!page_path) {
  console.error('usage: node render.mjs <page.html> [--size WxH]')
  process.exit(2)
}
const sizeArg = rest.includes('--size') ? rest[rest.indexOf('--size') + 1] : '1080x1080'
const [width, height] = sizeArg.split('x').map(Number)

const dir = dirname(page_path)
const name = basename(page_path).replace(/\.html$/, '')
mkdirSync(join(dir, 'png'), { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 })
await page.goto(`${pathToFileURL(page_path)}?w=${width}&h=${height}`)
await page.evaluate(() => document.fonts.ready)
await page.waitForTimeout(300)

const slides = page.locator('.slide')
const n = await slides.count()
for (let i = 0; i < n; i++) {
  await slides.nth(i).screenshot({ path: join(dir, 'png', `${name}-${String(i + 1).padStart(2, '0')}.png`) })
}

const overflow = await page.evaluate(() =>
  [...document.querySelectorAll('.box, .pad')]
    .map((b, i) => ({ frame: i + 1, over: b.scrollHeight - b.clientHeight }))
    .filter((o) => o.over > 0),
)
console.log('overflow', JSON.stringify(overflow))

if (n > 1) {
  await page.emulateMedia({ media: 'print' })
  await page.pdf({
    path: join(dir, `${name}.pdf`),
    width: `${width}px`,
    height: `${height}px`,
    printBackground: true,
    preferCSSPageSize: true,
    margin: { top: 0, right: 0, bottom: 0, left: 0 },
  })
}
await browser.close()
console.log('slides', n)
