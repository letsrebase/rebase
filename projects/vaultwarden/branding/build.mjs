// Draws the rebase look of the members' password manager out of `shared/brand`, so that
// nothing here restates a colour or a logo (REB-688).
//
//   node projects/vaultwarden/branding/build.mjs           write the generated files
//   node projects/vaultwarden/branding/build.mjs --check   fail when a committed file is
//                                                          not what the brand produces now
//
// What it writes, all of it committed. The deploy rsyncs the whole checkout, but the compose
// file mounts from this directory only, so the mounts need nothing from outside it:
//
//   templates/scss/user.vaultwarden.scss.hbs   the server compiles it and appends it to
//       /css/vaultwarden.css, which every page loads (`TEMPLATES_FOLDER`, compose file):
//       the palette on the web vault's own custom properties, and the lockup in place of
//       the Vaultwarden wordmark of the pre-login header, the sidebar and the splash.
//   images/*                                   the favicons, mounted over the image's own
//       (compose file), drawn from the four tiles of the mark.
//   branding.env                               a hash of all of the above, so that a new
//       look recreates the container (see `revision()`).
//
// The PNGs and the .ico come from a browser (`@playwright/test`, through the brand package)
// and are not byte-stable across Chromium versions, so `--check` compares the text files,
// asks that the images exist at the sizes the compose file mounts them as, and that
// `branding.env` is the hash of the files as committed. A palette or lockup change that
// leaves the favicons stale is not caught: redraw them with
// `pnpm --filter @rebase/brand exec node ../../projects/vaultwarden/branding/build.mjs`
// whenever the mark changes.
import { createHash } from 'node:crypto'
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
    // The lockup, which is drawn in these two inks, on the three grounds the web vault puts
    // it on. #e6ecf2 is the light Admin Console sidebar.
    ['prussian-blue', 'paper', 'the lockup in the pre-login header'],
    ['prussian-blue', '#e6ecf2', 'the lockup on the Admin Console sidebar'],
    ['paper', 'nav-bg-primary', 'the lockup on the vault sidebar'],
    // The mails, which have no dark theme: ink on their white card, the small print on paper.
    ['prussian-blue', '#ffffff', 'the text of a mail'],
    ['charcoal-blue', 'paper', 'the small print of a mail'],
    ['#ffffff', 'watermelon-deep', 'the button of the invitation'],
  ],
  dark: [
    ['fg-brand', '#0f172b', 'a link on the dark ground'],
    ['fg-brand', '#1d293d', 'a link on a dark card'],
    ['#0f172b', 'bg-brand', 'the label of a primary button'],
    ['#0f172b', 'bg-brand-strong', 'the label of a hovered primary button'],
    ['fg-brand', 'bg-brand-soft', 'a link on a selected row'],
    ['#ffffff', 'nav-bg-primary', 'the sidebar text'],
    ['#ffffff', 'nav-bg-primary-strong', 'the active sidebar item'],
    // The lockup, which is drawn in these two inks (README of shared/brand), on the three
    // grounds the web vault puts it on. #121a27 is the dark page ground, #1d293d the dark
    // Admin Console sidebar.
    ['paper', '#121a27', 'the lockup in the pre-login header'],
    ['paper', '#1d293d', 'the lockup on the Admin Console sidebar'],
    ['paper', 'nav-bg-primary', 'the lockup on the vault sidebar'],
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

// A lockup carries its design notes as an XML comment and pretty-printing; none of that
// belongs in a stylesheet every visitor downloads.
const compact = (svg) => svg.replace(/<!--[\s\S]*?-->/g, '').replace(/>\s+</g, '><').trim()
const dataUri = (svg) => `data:image/svg+xml;base64,${Buffer.from(compact(svg)).toString('base64')}`
const read = (f) => readFileSync(join(BRAND, f), 'utf8')

// Both lockups share one viewBox: the mark and the word at cap height, then the graft
// under it, so the picture is wider than tall (3943.5 by 945).
for (const f of ['lockup.svg', 'lockup-paper.svg']) {
  if (!read(f).includes('viewBox="0 0 3943.5 945"')) {
    throw new Error(`${f} changed shape: update the aspect ratio in build.mjs`)
  }
}
const ratio = '3943.5 / 945'

// The four tiles of a lockup, which is where the mark's order and colours live.
function tilesOf(source) {
  const tiles = [...read(source).matchAll(/<rect x="(\d+)" y="(\d+)" width="350" height="350" fill="(#[0-9a-f]{6})"\/>/g)]
  if (tiles.length !== 4) throw new Error(`${source} has ${tiles.length} tiles, expected 4`)
  return tiles.map(([, x, y, fill]) => ({ x: Number(x), y: Number(y), fill }))
}

// The mark alone, 700 by 700 with `pad` around it and an optional ground.
function markSvg({ pad = 0, ground = null, source = 'lockup.svg' } = {}) {
  const size = 700 + 2 * pad
  const rects = tilesOf(source)
    .map(({ x, y, fill }) => `<rect x="${x + pad}" y="${y + pad}" width="350" height="350" fill="${fill}"/>`)
    .join('')
  const bg = ground ? `<rect width="${size}" height="${size}" fill="${ground}"/>` : ''
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" shape-rendering="crispEdges">${bg}${rects}</svg>`
}

export function scss() {
  // Each picture is a custom property holding its data URI once, so the stylesheet does not
  // carry a lockup per rule.
  const pictures = {
    'lockup-ink': read('lockup.svg'),
    'lockup-paper': read('lockup-paper.svg'),
    'mark-ink': markSvg(),
    'mark-paper': markSvg({ source: 'lockup-paper.svg' }),
  }
  const defs = Object.entries(pictures)
    .map(([name, svg]) => `  --rebase-${name}: url('${dataUri(svg)}');`)
    .join('\n')
  const fill = (name) => `  background-image: var(--rebase-${name});`
  const content = (name) => `  content: var(--rebase-${name});`
  const lockupInk = fill('lockup-ink')
  const lockupPaper = fill('lockup-paper')
  const markInk = fill('mark-ink')
  const markPaper = fill('mark-paper')
  // The sidebar of the Admin Console is light in the light theme (`variant="secondary"`),
  // the one of the vault is dark in both; a collapsed sidebar has room for the mark only,
  // and says so with the `!tw-h-[55px]` class on its link.
  const collapsed = 'a[class~="!tw-h-[55px]"]'
  const lightConsole = '.theme_light app-organization-layout'
  return `// Generated by projects/vaultwarden/branding/build.mjs from shared/brand. Edit that, never this file.
// The server appends this to /css/vaultwarden.css (README, "Look").

:root {
${defs}
}

.theme_light {
${block('light')}
}

.theme_dark {
${block('dark')}
}

// The wordmark of the pre-login header, the sidebar and the page that shows before the
// app has started. The web vault draws the first two as an inline <svg>, so they cannot
// be swapped for a file: the drawing is hidden and the lockup takes the box.
bit-landing-header header a bit-svg,
bit-nav-logo a bit-svg {
  width: 100%;
  aspect-ratio: ${ratio};
  background: no-repeat left center / contain;
}
bit-landing-header header a bit-svg > svg,
bit-nav-logo a bit-svg > svg {
  display: none;
}
bit-landing-header header a bit-svg {
${lockupInk}
}
.theme_dark bit-landing-header header a bit-svg,
bit-nav-logo a bit-svg {
${lockupPaper}
}
${lightConsole} bit-nav-logo a bit-svg {
${lockupInk}
}
bit-nav-logo ${collapsed} bit-svg {
  width: 1.625rem;
  aspect-ratio: 1;
${markPaper}
}
${lightConsole} bit-nav-logo ${collapsed} bit-svg {
${markInk}
}
app-root img.new-logo-themed {
${content('lockup-ink')}
}
.theme_dark app-root img.new-logo-themed {
${content('lockup-paper')}
}
`
}

// Safari's pinned tab takes a single-colour mask, whatever colour the tiles had: the two
// ink tiles of the mark, which sit on a diagonal.
export function pinnedSvg() {
  const ink = token('prussian-blue')
  const rects = tilesOf('lockup.svg')
    .filter(({ fill }) => fill === ink)
    .map(({ x, y }) => `<rect x="${x}" y="${y}" width="350" height="350"/>`)
    .join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 700 700" fill="#000">${rects}</svg>\n`
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

// The mails (REB-690). Vaultwarden's own header and footer attach the Vaultwarden wordmark
// and a GitHub icon that links to upstream's repository, and every mail it sends has the
// shape of every other self-hosted instance's: Gmail put the first invitation in Spam as
// «similar to messages identified as spam in the past» although SPF, DKIM and DMARC all
// passed. These partials draw the mark from table cells, in the palette, with no image
// and no link to anywhere but the page the mail is about; with `SMTP_EMBED_IMAGES=false`
// (compose file) the mail carries no attachment either.
const mail = () => ({
  ink: token('prussian-blue'),
  paper: token('paper'),
  charcoal: token('charcoal-blue'),
  gold: token('royal-gold'),
  watermelon: token('watermelon'),
  action: token('watermelon-deep'),
})

export function emailHeader() {
  const c = mail()
  const cell = (bg) => `<td width="14" height="14" bgcolor="${bg}" style="width:14px;height:14px;font-size:0;line-height:0">&nbsp;</td>`
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<title>Rebase</title>
</head>
<body bgcolor="${c.paper}" style="margin:0;padding:0;background-color:${c.paper};color:${c.ink};font-family:Helvetica,Arial,sans-serif;font-size:16px;line-height:24px">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" bgcolor="${c.paper}" style="background-color:${c.paper}">
<tr><td align="center" style="padding:24px 12px">
<table width="560" cellpadding="0" cellspacing="0" role="presentation" style="width:100%;max-width:560px">
<tr><td style="padding:0 0 16px 4px">
<table cellpadding="0" cellspacing="0" role="presentation"><tr>
<td valign="middle" style="padding-right:10px">
<table cellpadding="0" cellspacing="0" role="presentation" style="border-collapse:collapse">
<tr>${cell(c.ink)}${cell(c.gold)}</tr>
<tr>${cell(c.watermelon)}${cell(c.ink)}</tr>
</table>
</td>
<td valign="middle" style="font-size:26px;line-height:28px;font-weight:bold;letter-spacing:-0.035em;color:${c.ink}">rebase</td>
</tr></table>
</td></tr>
<tr><td bgcolor="#ffffff" style="background-color:#ffffff;border-top:4px solid ${c.ink};padding:28px 24px;color:${c.ink};font-size:16px;line-height:24px">
`
}

export function emailFooter() {
  const c = mail()
  return `</td></tr>
<tr><td style="padding:16px 4px 0 4px;color:${c.charcoal};font-size:12px;line-height:18px">
Sent by the Rebase password manager. If you were not expecting this message you can ignore it.
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
`
}

export function emailFooterText() {
  return `
---
Sent by the Rebase password manager. If you were not expecting this message you can ignore it.
`
}

// The invitation, the one mail a person gets before they have an account: the same words
// as upstream's, the button in the brand's action colour and nothing else.
export function orgInviteHtml() {
  const c = mail()
  return `Join {{{org_name}}}
<!---------------->
{{> email/email_header }}
<p style="margin:0 0 16px 0;color:${c.ink}">You have been invited to join the <b>{{org_name}}</b> organization.</p>
<p style="margin:0 0 20px 0">
<a data-testid="invite" href="{{{url}}}" clicktracking=off target="_blank" style="display:inline-block;background-color:${c.action};color:#ffffff;text-decoration:none;font-weight:bold;padding:12px 24px;border-radius:4px">Join {{org_name}}</a>
</p>
<p style="margin:0;color:${c.charcoal};font-size:14px;line-height:20px">If you do not wish to join this organization, you can safely ignore this email.</p>
{{> email/email_footer }}
`
}

const texts = {
  'templates/scss/user.vaultwarden.scss.hbs': scss,
  'images/safari-pinned-tab.svg': pinnedSvg,
  'templates/email/email_header.hbs': emailHeader,
  'templates/email/email_footer.hbs': emailFooter,
  'templates/email/email_footer_text.hbs': emailFooterText,
  'templates/email/send_org_invite.html.hbs': orgInviteHtml,
}
const GENERATED = [...Object.keys(texts), ...IMAGES.map(([file]) => file), 'images/favicon.ico'].sort()

// A hash of every file the container mounts. Docker Compose recreates a service when its
// own configuration changes and not when the content of a bind-mounted file does, and the
// server reads its templates once at start and caches the compiled stylesheet, so a deploy
// of a new look would go green and change nothing. The hash rides in an `env_file`, which
// is part of the configuration, so a new look means a new container.
export function revision() {
  const hash = createHash('sha256')
  for (const file of GENERATED) hash.update(file).update(readFileSync(join(HERE, file)))
  return hash.digest('hex').slice(0, 16)
}
const envFile = () =>
  `# Generated by branding/build.mjs: a hash of the files this directory mounts into the server,\n# so that changing the look recreates the container (README, "Look"). Never edit it.\nBRANDING_REV=${revision()}\n`

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
    const envPath = join(HERE, 'branding.env')
    if (bad === 0 && (!existsSync(envPath) || readFileSync(envPath, 'utf8') !== envFile())) {
      console.error('branding/branding.env does not match the files it hashes: run build.mjs')
      bad++
    }
    process.exit(bad ? 1 : 0)
  }
  for (const [file, make] of Object.entries(texts)) {
    mkdirSync(dirname(join(HERE, file)), { recursive: true })
    writeFileSync(join(HERE, file), make())
  }
  await writeImages()
  writeFileSync(join(HERE, 'branding.env'), envFile())
  console.log('branding written')
}
