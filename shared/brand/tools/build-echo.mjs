/**
 * Draws the echo logo: «rebase» solid, with three outlined copies of the word stacked
 * above it, each laid over the one before like a cut-out (ORB-199, ORB-200). Writes the
 * six variants into `shared/brand/echo/`.
 *
 * Not part of any build, and run by hand: `pnpm --filter @rebase/brand build:echo`.
 * The PNGs are committed, so a surface that shows the logo never waits on a render and
 * the file that ships is the file somebody looked at. A directory given as the first
 * argument receives the six files instead, so what this draws today can be compared
 * with what is committed without overwriting it.
 *
 * The colours come out of `palette.css` and the face out of `fonts/`, never restated
 * here. The two literals are white and black, which the palette does not carry
 * (`--color-paper` is a grey, `--color-prussian-blue` a blue): white is what the chosen
 * dark-ground variant was drawn in, and black is for print and for a surface that shows
 * the logo in one colour it does not own, a partner's page or a monochrome document.
 *
 * How the picture is built, since each rule below fixed something that looked wrong:
 *
 * - Outfit 700 at -0.01em, 400px, three copies each 0.22em above the last.
 * - An outline is not a stroke of the glyph. Outfit builds some letters from overlapping
 *   pieces (the bar of the e, the stems of r, b and a) and a stroke draws those seams.
 *   The outline is the ring between the filled letter grown and shrunk by half a line.
 * - A lower copy hides what of the upper copies falls inside its letters, and a letter
 *   covers more than its ink: counters (e, b, a), the open bays of the s, the tail of
 *   the s where it sticks out past the bowl above it, and any notch under an arm. So no
 *   line ever crosses another and no piece of a copy peeks through a letter in front.
 * - The cover reaches half a line past each letter, and the solid word is grown by the
 *   same half line, so an outline that runs into the letter in front meets its edge with
 *   no gap and no sliver.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const brand = join(dirname(fileURLToPath(import.meta.url)), '..')
const out = process.argv[2] ?? join(brand, 'echo')

function token(name) {
  const css = readFileSync(join(brand, 'palette.css'), 'utf-8')
  const value = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{3,8});`))?.[1]
  if (!value) throw new Error(`shared/brand/palette.css declares no --color-${name}`)
  return value
}

const ink = token('prussian-blue')
const watermelon = token('watermelon')
const white = '#ffffff'
const black = '#000000'

/** `word` is the solid word's colour, `outline` the copies'. */
export const VARIANTS = [
  { file: 'echo-ink-watermelon-outlines.png', word: ink, outline: watermelon, ground: 'light' },
  { file: 'echo-white.png', word: white, outline: white, ground: 'dark' },
  { file: 'echo-ink.png', word: ink, outline: ink, ground: 'light' },
  { file: 'echo-watermelon.png', word: watermelon, outline: watermelon, ground: 'light' },
  { file: 'echo-watermelon-white-outlines.png', word: watermelon, outline: white, ground: 'dark' },
  { file: 'echo-black.png', word: black, outline: black, ground: 'light' },
]

function markup({ word, outline }) {
  const font = readFileSync(join(brand, 'fonts', 'outfit-variable-latin.woff2')).toString('base64')
  return `<!doctype html>
<html>
  <head>
    <meta charset="UTF-8" />
    <style>
      @font-face { font-family: 'Outfit'; font-weight: 300 700; src: url(data:font/woff2;base64,${font}) format('woff2'); }
      html, body { margin: 0; background: transparent; }
      svg { display: block; }
      svg text { font-family: 'Outfit'; font-weight: 700; letter-spacing: -0.01em; }
    </style>
  </head>
  <body>
    <svg id="logo" xmlns="http://www.w3.org/2000/svg"></svg>
    <script>
      const WORD = 'rebase', COPIES = 3, SIZE = 400, STEP = 0.22 * SIZE, LINE = 3, PAD = 24
      const wordColour = ${JSON.stringify(word)}, outlineColour = ${JSON.stringify(outline)}
      const NS = 'http://www.w3.org/2000/svg'
      const svg = document.getElementById('logo')
      const el = (name, attrs, parent = svg) => {
        const node = document.createElementNS(NS, name)
        for (const k in attrs) node.setAttribute(k, attrs[k])
        parent.appendChild(node)
        return node
      }

      window.drawn = document.fonts.load('700 ' + SIZE + 'px Outfit').then(() => {
        const probe = el('text', { x: 0, y: 0, 'font-size': SIZE })
        probe.textContent = WORD
        const box = probe.getBBox()
        probe.remove()
        // Ascender to baseline: «rebase» has no descender.
        const inkTop = -0.74 * SIZE
        const width = Math.ceil(box.width + PAD * 2)
        const baseline = PAD + LINE + COPIES * STEP - inkTop
        const height = Math.ceil(baseline + PAD)
        for (const [k, v] of Object.entries({ width, height, viewBox: '0 0 ' + width + ' ' + height })) svg.setAttribute(k, v)

        const defs = el('defs', {})
        const x = PAD - box.x
        const text = (parent, i, attrs) => {
          el('text', { x, y: baseline - i * STEP, 'font-size': SIZE, ...attrs }, parent).textContent = WORD
        }

        // The cover of one copy, rasterised letter by letter at twice the resolution and
        // drawn black on transparent, so it works directly as a mask layer.
        const S = 2
        const glyphs = el('text', { x, y: baseline, 'font-size': SIZE })
        glyphs.textContent = WORD
        const font = '700 ' + SIZE + 'px Outfit'
        const canvas = document.createElement('canvas')
        canvas.width = width * S
        canvas.height = height * S
        const ctx = canvas.getContext('2d')
        ctx.font = font
        const xHeight = ctx.measureText('x').actualBoundingBoxAscent
        const H = canvas.height
        const band = Math.round((baseline - xHeight * 0.8) * S)
        const mid = Math.round((baseline - xHeight / 2) * S)
        const edge = LINE / 2
        for (let k = 0; k < WORD.length; k++) {
          const ext = glyphs.getExtentOfChar(k)
          const left = Math.floor(ext.x - SIZE * 0.1)
          const strip = document.createElement('canvas')
          strip.width = Math.ceil(ext.width + SIZE * 0.2) * S
          strip.height = H
          const o = strip.getContext('2d')
          o.scale(S, S)
          o.font = font
          o.lineWidth = edge * 2
          o.lineJoin = 'round'
          const cx = glyphs.getStartPositionOfChar(k).x - left
          o.fillText(WORD[k], cx, baseline)
          o.strokeText(WORD[k], cx, baseline)
          const w = strip.width
          const img = o.getImageData(0, 0, w, H)
          const px = img.data
          const inked = (xx, yy) => px[(yy * w + xx) * 4 + 3] >= 24
          // Row by row, leftmost to rightmost ink: closes counters and open bays.
          const spans = []
          let upMin = w, upMax = -1, loMin = w, loMax = -1
          for (let yy = 0; yy < H; yy++) {
            let a = -1, b = -1
            for (let xx = 0; xx < w; xx++) if (inked(xx, yy)) { if (a < 0) a = xx; b = xx }
            if (a < 0) continue
            spans.push([yy, a, b])
            if (yy < mid) { upMin = Math.min(upMin, a); upMax = Math.max(upMax, b) }
            else { loMin = Math.min(loMin, a); loMax = Math.max(loMax, b) }
          }
          // A tail: the lower half reaching past the upper half by more than two lines.
          // From just under the x-height line the cover spans the tail's width too.
          const tail = LINE * 2 * S
          const padL = loMin < upMin - tail ? loMin : w
          const padR = loMax > upMax + tail ? loMax : -1
          for (const [yy, a, b] of spans) {
            const from = yy >= band ? Math.min(a, padL) : a
            const to = yy >= band ? Math.max(b, padR) : b
            for (let xx = from; xx <= to; xx++) px[(yy * w + xx) * 4 + 3] = 255
          }
          // Column by column, from the letter's top edge down to its foot: a notch under
          // an arm hides what is behind it as well.
          const foot = spans.length ? spans[spans.length - 1][0] : -1
          for (let xx = 0; xx < w; xx++) {
            let yy = 0
            while (yy <= foot && !inked(xx, yy)) yy++
            for (; yy <= foot; yy++) px[(yy * w + xx) * 4 + 3] = 255
          }
          for (let i = 0; i < px.length; i += 4) px[i] = px[i + 1] = px[i + 2] = 0
          o.setTransform(1, 0, 0, 1, 0, 0)
          o.putImageData(img, 0, 0)
          ctx.drawImage(strip, left * S, 0)
        }
        const cover = canvas.toDataURL('image/png')
        glyphs.remove()

        // The outline: the ring between the filled letter grown and shrunk by half a line.
        const ring = el('filter', { id: 'outline', filterUnits: 'userSpaceOnUse', x: 0, y: 0, width, height }, defs)
        el('feMorphology', { in: 'SourceAlpha', operator: 'dilate', radius: edge, result: 'grown' }, ring)
        el('feMorphology', { in: 'SourceAlpha', operator: 'erode', radius: edge, result: 'shrunk' }, ring)
        el('feComposite', { in: 'grown', in2: 'shrunk', operator: 'out', result: 'ring' }, ring)
        el('feFlood', { 'flood-color': outlineColour, result: 'colour' }, ring)
        el('feComposite', { in: 'colour', in2: 'ring', operator: 'in' }, ring)

        // Copy i is hidden by the covers of every copy below it, the solid word included.
        for (let i = COPIES; i >= 1; i--) {
          const mask = el('mask', { id: 'hide-' + i, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width, height }, defs)
          el('rect', { x: 0, y: 0, width, height, fill: '#fff' }, mask)
          for (let j = 0; j < i; j++) el('image', { href: cover, x: 0, y: -j * STEP, width, height, preserveAspectRatio: 'none' }, mask)
          text(el('g', { mask: 'url(#hide-' + i + ')' }), i, { fill: '#000', filter: 'url(#outline)' })
        }
        text(svg, 0, { fill: wordColour, stroke: wordColour, 'stroke-width': LINE, 'stroke-linejoin': 'round' })
      })
    </script>
  </body>
</html>`
}

const browser = await chromium.launch()
const engine = browser.version()
mkdirSync(out, { recursive: true })
for (const variant of VARIANTS) {
  // A page each: the drawing script declares its constants at the top level, and a second
  // setContent on the same page would declare them again.
  // Twice the CSS pixels, so the files are about 2500px wide: enough for a cover or a slide.
  const page = await browser.newPage({ viewport: { width: 2000, height: 1200 }, deviceScaleFactor: 2 })
  page.on('pageerror', (error) => console.error(error.message))
  await page.setContent(markup(variant), { waitUntil: 'load' })
  await page.evaluate(() => window.drawn)
  // Snyk Code javascript/PT here is a false positive: a developer's script, writing
  // where the developer running it asks it to.
  writeFileSync(join(out, variant.file), await page.locator('#logo').screenshot({ omitBackground: true }))
  await page.close()
  console.log(`${variant.file}: chromium ${engine} on ${process.platform} -> ${out}`)
}
await browser.close()
