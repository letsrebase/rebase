/**
 * Draws the image a link to this site shares with, and writes it to
 * `src/public/assets/share-card-<N>.png` (ORB-112).
 *
 * Not part of the build, and run by hand: the card is a static asset committed to the
 * repository, as the card asked, so a visitor's share never waits on a render and the
 * file that ships is the file somebody looked at. This script exists so that the next
 * person who has to change the wordmark, the claim or the palette changes one line and
 * runs `pnpm --filter website build:share-card` instead of opening a design tool.
 *
 * **The file name carries a number, and a redraw takes the next one.** LinkedIn, Facebook
 * and WhatsApp cache what they scraped per URL for days, so a card redrawn under its old
 * name keeps sharing as the old card long after it shipped, on exactly the surfaces this
 * heads, and it is the whole of the cache invalidation. The rename of the copy to
 * Rebase (ORB-194) shipped in the heads and the markup; the wordmark itself moved off
 * plain text and onto the brand's own outlines in REB-205.
 *
 * A path given as the first argument is rendered there instead, so the committed file
 * can be compared with what this produces today without being overwritten.
 *
 * The colours are read out of `shared/brand/palette.css` and the typeface out of
 * `shared/brand/fonts`, never restated here: a hex typed into this file would be the
 * fork that `palette-plugin.ts` exists to prevent, one directory away. `share-card.test.ts`
 * fails this file if a hex appears in it.
 *
 * 1200x630 is what every client that shows a large card wants (LinkedIn, WhatsApp,
 * Slack, X). `deviceScaleFactor: 1`, so the pixels are the CSS pixels laid out below.
 */
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))
const brand = join(here, '..', '..', '..', 'shared', 'brand')
/** Bumped on every redraw: see the note above about what the platforms cache. */
export const CARD_FILE = 'share-card-2.png'
const out = process.argv[2] ?? join(here, '..', 'src', 'public', 'assets', CARD_FILE)

export const WIDTH = 1200
export const HEIGHT = 630

/** The tokens this card paints with, read from the shared palette. */
function palette() {
  const css = readFileSync(join(brand, 'palette.css'), 'utf-8')
  const token = (name) => {
    const value = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{3,8});`))?.[1]
    if (!value) throw new Error(`shared/brand/palette.css declares no --color-${name}`)
    return value
  }
  return { ink: token('prussian-blue'), gold: token('royal-gold'), melon: token('watermelon') }
}

function markup() {
  const { ink, gold, melon } = palette()
  // The paper cut, for this dark ground: the same outlines `wordmark.svg` draws for a
  // light one, generated together by `shared/brand/tools/build-wordmark.py` and never
  // hand-edited. Read at build time, never restated as a string of text here, so the
  // card draws the one word the brand owns rather than a font-rendered guess at it.
  const wordmark = readFileSync(join(brand, 'wordmark-paper.svg'), 'utf-8')
  const font = readFileSync(join(brand, 'fonts', 'outfit-variable-latin.woff2')).toString('base64')
  // The mark on the ink ground, which is where the four tiles need the treatment
  // `pitch.css` already gives them in its dark slides: the two Prussian Blue tiles are
  // drawn white, because on their own colour they would not be there at all.
  return `<!doctype html>
<html lang="it">
  <head>
    <meta charset="UTF-8" />
    <style>
      @font-face {
        font-family: 'Outfit';
        font-weight: 300 700;
        src: url(data:font/woff2;base64,${font}) format('woff2');
      }
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body {
        width: ${WIDTH}px;
        height: ${HEIGHT}px;
        background-color: ${ink};
        /* The faint grid the whole visual system sits on, at the scale of a card this
           size rather than of a page. */
        /* White at 5%, not a token: the grid line is the ink lightened by the ground it
           sits on, which is what system.css does with its own line on paper. A backtick
           has no business in here either way, this block is inside a template literal. */
        background-image:
          linear-gradient(to right, rgba(255, 255, 255, 0.05) 1px, transparent 1px),
          linear-gradient(to bottom, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
        background-size: 42px 42px;
        color: #ffffff;
        font-family: 'Outfit', sans-serif;
        display: flex;
        flex-direction: column;
        justify-content: center;
        gap: 30px;
        padding: 96px;
      }
      /* Four tiles, at the scale of the card: white, gold, watermelon, white. */
      .glyph {
        width: 44px;
        height: 44px;
        background-color: #ffffff;
        box-shadow:
          44px 0 0 ${gold},
          0 44px 0 ${melon},
          44px 44px 0 #ffffff;
        margin-bottom: 44px;
      }
      /* The SVG carries no width or height of its own (README: the same file is the
         18px header chip and a 1584px cover), so the box here is what sizes it; the
         line-box the text version used to fill. */
      .wordmark { height: 128px; }
      .wordmark svg { display: block; height: 100%; width: auto; }
      /* One line, never two: the claim is a sentence and a card that breaks it after
         «da» reads as a layout accident. At this size it measures about 640px of the
         1008px the padding leaves. */
      .claim { font-size: 54px; font-weight: 300; line-height: 1.1; white-space: nowrap; }
      .foot { font-size: 30px; font-weight: 400; color: ${gold}; letter-spacing: 0.01em; }
    </style>
  </head>
  <body>
    <div class="glyph"></div>
    <div class="wordmark">${wordmark}</div>
    <p class="claim">freelance, ma non da soli</p>
    <p class="foot">letsrebase.com</p>
  </body>
</html>`
}

const browser = await chromium.launch()
const engine = browser.version()
const page = await browser.newPage({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
})
await page.setContent(markup(), { waitUntil: 'load' })
await page.evaluate(() => document.fonts.ready.then(() => undefined))
mkdirSync(dirname(out), { recursive: true })
// Snyk Code javascript/PT here is a false positive: a developer's script, writing
// where the developer running it asks it to.
writeFileSync(out, await page.screenshot({ type: 'png' }))
await browser.close()
// Which browser drew it and where: glyph rasterization differs between macOS and
// Linux, so a redraw on another box is a binary diff with no visible change, and this
// is the line to quote in the pull request that carries one.
console.log(`${CARD_FILE}: ${WIDTH}x${HEIGHT}, chromium ${engine} on ${process.platform} -> ${out}`)
