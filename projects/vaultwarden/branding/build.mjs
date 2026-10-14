// Draws the rebase look of the members' password manager out of `shared/brand`, so that
// nothing here restates a colour or a logo (REB-688).
//
//   node projects/vaultwarden/branding/build.mjs           write the generated files
//   node projects/vaultwarden/branding/build.mjs --check   fail when a committed file is
//                                                          not what the brand produces now
//
// What it writes, all of it committed so the deploy rsyncs this directory and nothing
// reaches outside it:
//
//   templates/scss/user.vaultwarden.scss.hbs   the server compiles it and appends it to
//       /css/vaultwarden.css, which every page loads (`TEMPLATES_FOLDER`, compose file):
//       the palette on the web vault's own custom properties, and the lockup in place of
//       the Vaultwarden wordmark of the pre-login header and of the sidebar.
//   images/*                                   the favicons, mounted over the image's own
//       (compose file), drawn from the four tiles of the mark.
//
// The PNGs and the .ico come from a browser (`@playwright/test`, through the brand package)
// and are not byte-stable across Chromium versions, so `--check` compares the text files
// and only asks that the images exist at the sizes the compose file mounts them as.
// Redraw them with `pnpm --filter @rebase/brand exec node ../../projects/vaultwarden/branding/build.mjs`
// when the mark changes.
import { createRequire } from 'node:module'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..', '..', '..')
const BRAND = join(ROOT, 'shared', 'brand')
const CHECK = process.argv.includes('--check')

const palette = readFileSync(join(BRAND, 'palette.css'), 'utf8')
export function token(name) {
  const m = palette.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})\\b`))
  if (!m) throw new Error(`--color-${name} is not in shared/brand/palette.css`)
  return m[1].toLowerCase()
}
const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(' ')

// The web vault paints itself from two generations of custom property, and an override
// has to say both. The old one holds space separated RGB triplets
// (`--color-primary-600: 18 82 163`), the new one hex (`--color-bg-brand: var(--color-brand-700)`).
// Light theme in `:root`, dark in `.theme_dark`; each entry names the brand token it is.
export const THEMES = {
  light: {
    triplets: {
      // Links and buttons of the older components, and their hover.
      'primary-600': 'watermelon-deep',
      'primary-700': 'prussian-blue',
      'primary-100': 'paper',
      'background-alt': 'paper',
      'background-alt3': 'prussian-blue',
      'background-alt4': 'charcoal-blue',
      // The pictures on the pre-login pages.
      'illustration-outline': 'prussian-blue',
      'illustration-bg-primary': 'royal-gold',
      'illustration-bg-secondary': 'paper',
      'illustration-tertiary': 'watermelon',
      'illustration-logo': 'watermelon',
    },
    hex: {
      // The primary button, its hover, a link and an avatar.
      'bg-brand': 'watermelon-deep',
      'bg-brand-strong': 'prussian-blue',
      'bg-brand-stronger': 'prussian-blue',
      'bg-brand-soft': 'paper',
      'bg-brand-softer': 'paper',
      'bg-brand-medium': 'paper',
      'border-brand': 'watermelon-deep',
      'border-brand-strong': 'prussian-blue',
      'fg-brand': 'watermelon-deep',
      'fg-brand-strong': 'prussian-blue',
      'bg-avatar-brand': 'watermelon-deep',
      'bg-avatar-brand-hover': 'prussian-blue',
      // The sidebar and its active item.
      'nav-bg-primary': 'prussian-blue',
      'nav-bg-primary-strong': 'charcoal-blue',
    },
  },
  dark: {
    triplets: {
      'primary-600': 'royal-gold',
      'primary-700': 'paper',
      'primary-100': 'charcoal-blue',
      'primary-300': 'charcoal-blue',
    },
    hex: {
      'bg-brand': 'royal-gold',
      'bg-brand-strong': 'paper',
      'bg-brand-stronger': 'paper',
      'bg-brand-soft': 'charcoal-blue',
      'bg-brand-softer': 'charcoal-blue',
      'bg-brand-medium': 'charcoal-blue',
      'border-brand': 'royal-gold',
      'border-brand-strong': 'paper',
      'fg-brand': 'royal-gold',
      'fg-brand-strong': 'paper',
      'bg-avatar-brand': 'royal-gold',
      'bg-avatar-brand-hover': 'paper',
      'nav-bg-primary': 'prussian-blue',
      'nav-bg-primary-strong': 'charcoal-blue',
    },
  },
}

// The grounds the web vault draws under those colours, which are its own values and not
// ours (gray-900 is #0f172b, gray-800 #1d293d, white is white), so the test measures our
// colours against them. A pair is `[foreground, background, what it is]`; a bare name is
// a hex entry of the theme.
export const PAIRS = {
  light: [
    ['fg-brand', '#ffffff', 'a link on a card'],
    ['fg-brand', 'paper', 'a link on the page ground'],
    ['fg-brand', 'bg-brand-soft', 'a link on a selected row'],
    ['#ffffff', 'bg-brand', 'the label of a primary button'],
    ['#ffffff', 'bg-brand-strong', 'the label of a hovered primary button'],
    ['#ffffff', 'nav-bg-primary', 'the sidebar text'],
    ['#ffffff', 'nav-bg-primary-strong', 'the active sidebar item'],
  ],
  dark: [
    ['fg-brand', '#0f172b', 'a link on the dark ground'],
    ['fg-brand', '#1d293d', 'a link on a dark card'],
    ['#0f172b', 'bg-brand', 'the label of a primary button'],
    ['#0f172b', 'bg-brand-strong', 'the label of a hovered primary button'],
    ['fg-brand', 'bg-brand-soft', 'a link on a selected row'],
    ['#ffffff', 'nav-bg-primary', 'the sidebar text'],
    ['#ffffff', 'nav-bg-primary-strong', 'the active sidebar item'],
  ],
}

const resolve = (theme, ref) => (ref.startsWith('#') ? ref : token(THEMES[theme].hex[ref] ?? ref))
export const resolvePair = (theme, [fg, bg]) => [resolve(theme, fg), resolve(theme, bg)]

function block(theme) {
  const { triplets, hex } = THEMES[theme]
  return [
    ...Object.entries(triplets).map(([name, tok]) => `  --color-${name}: ${rgb(token(tok))}; // ${tok}`),
    ...Object.entries(hex).map(([name, tok]) => `  --color-${name}: ${token(tok)}; // ${tok}`),
  ].join('\n')
}

const dataUri = (svg) => `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`
const read = (f) => readFileSync(join(BRAND, f), 'utf8')

// Both lockups share one viewBox: the mark and the word at cap height, then the graft
// under it, so the picture is wider than tall (3943.5 by 945).
for (const f of ['lockup.svg', 'lockup-paper.svg']) {
  if (!read(f).includes('viewBox="0 0 3943.5 945"')) {
    throw new Error(`${f} changed shape: update the aspect ratio in build.mjs`)
  }
}
const ratio = '3943.5 / 945'

export function scss() {
  const logo = (file) => `  background: url('${dataUri(read(file))}') no-repeat left center / contain;`
  return `// Generated by projects/vaultwarden/branding/build.mjs from shared/brand. Edit that, never this file.
// The server appends this to /css/vaultwarden.css (README, "Look").

.theme_light {
${block('light')}
}

.theme_dark {
${block('dark')}
}

// The wordmark of the pre-login header and of the sidebar. The web vault draws it as an
// inline <svg>, so it cannot be swapped for a file: the drawing is hidden and the lockup
// takes its box.
bit-landing-header header a bit-svg,
bit-nav-logo a bit-svg {
  display: block;
  width: 100%;
  aspect-ratio: ${ratio};
  max-height: 3rem;
}
bit-landing-header header a bit-svg > svg,
bit-nav-logo a bit-svg > svg {
  display: none;
}
bit-landing-header header a bit-svg {
${logo('lockup.svg')}
}
.theme_dark bit-landing-header header a bit-svg,
bit-nav-logo a bit-svg {
${logo('lockup-paper.svg')}
}
`
}

// The four tiles of the mark alone, 700 by 700, read out of the lockup so the order and
// the colours stay the brand's.
function markSvg({ pad = 0, ground = null } = {}) {
  const tiles = [...read('lockup.svg').matchAll(/<rect x="(\d+)" y="(\d+)" width="350" height="350" fill="(#[0-9a-f]{6})"\/>/g)]
  if (tiles.length !== 4) throw new Error(`lockup.svg has ${tiles.length} tiles, expected 4`)
  const size = 700 + 2 * pad
  const rects = tiles
    .map(([, x, y, , fill]) => `<rect x="${Number(x) + pad}" y="${Number(y) + pad}" width="350" height="350" fill="${fill}"/>`)
    .join('')
  const bg = ground ? `<rect width="${size}" height="${size}" fill="${ground}"/>` : ''
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" shape-rendering="crispEdges">${bg}${rects}</svg>`
}

// Safari's pinned tab takes a single-colour mask, whatever colour the tiles had.
export function pinnedSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 700 700"><path fill="#000" d="M0 0h350v350H0zM350 350h350v350H350zM0 350h350v350H0z"/></svg>\n`
}

export const IMAGES = [
  // file, pixel size, padding in mark units, ground
  ['images/favicon-16x16.png', 16, 0, null],
  ['images/favicon-32x32.png', 32, 0, null],
  ['images/apple-touch-icon.png', 180, 175, 'paper'],
  ['images/icons/android-chrome-192x192.png', 192, 175, 'paper'],
  ['images/icons/android-chrome-512x512.png', 512, 175, 'paper'],
]

function pngSize(file) {
  const b = readFileSync(file)
  return [b.readUInt32BE(16), b.readUInt32BE(20)]
}

async function writeImages() {
  const { chromium } = createRequire(join(BRAND, 'package.json'))('@playwright/test')
  const browser = await chromium.launch()
  try {
    for (const [file, size, pad, ground] of IMAGES) {
      const page = await browser.newPage({ viewport: { width: size, height: size } })
      const svg = markSvg({ pad, ground: ground && token(ground) })
      await page.setContent(
        `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
      )
      const out = join(HERE, file)
      mkdirSync(dirname(out), { recursive: true })
      writeFileSync(out, await page.screenshot({ omitBackground: true, type: 'png' }))
      await page.close()
    }
  } finally {
    await browser.close()
  }
  // A .ico may carry a PNG as its image: one 32 pixel entry is what the web vault's
  // /favicon.ico needs.
  const png = readFileSync(join(HERE, 'images/favicon-32x32.png'))
  const head = Buffer.alloc(22)
  head.writeUInt16LE(1, 2)
  head.writeUInt16LE(1, 4)
  head[6] = 32
  head[7] = 32
  head.writeUInt16LE(1, 10)
  head.writeUInt16LE(32, 12)
  head.writeUInt32LE(png.length, 14)
  head.writeUInt32LE(22, 18)
  writeFileSync(join(HERE, 'images/favicon.ico'), Buffer.concat([head, png]))
}

const texts = {
  'templates/scss/user.vaultwarden.scss.hbs': scss,
  'images/safari-pinned-tab.svg': pinnedSvg,
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (CHECK) {
    let bad = 0
    for (const [file, make] of Object.entries(texts)) {
      const path = join(HERE, file)
      if (!existsSync(path) || readFileSync(path, 'utf8') !== make()) {
        console.error(`branding/${file} is not what shared/brand produces: run build.mjs`)
        bad++
      }
    }
    for (const [file, size] of IMAGES) {
      const path = join(HERE, file)
      if (!existsSync(path) || pngSize(path).join('x') !== `${size}x${size}`) {
        console.error(`branding/${file} is missing or not ${size}x${size}`)
        bad++
      }
    }
    if (!existsSync(join(HERE, 'images/favicon.ico'))) {
      console.error('branding/images/favicon.ico is missing')
      bad++
    }
    process.exit(bad ? 1 : 0)
  }
  for (const [file, make] of Object.entries(texts)) {
    mkdirSync(dirname(join(HERE, file)), { recursive: true })
    writeFileSync(join(HERE, file), make())
  }
  await writeImages()
  console.log('branding written')
}
