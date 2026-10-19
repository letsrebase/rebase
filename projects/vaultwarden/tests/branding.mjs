// What the look of the web vault (REB-688) must keep true, with no server and no browser:
//
//   node projects/vaultwarden/tests/branding.mjs
//
// 1. The committed files are what `shared/brand` produces now (branding/build.mjs --check),
//    so a palette or lockup change that nobody regenerated fails here.
// 2. Every colour pair the stylesheet creates is readable: text at WCAG AA (4.5:1), measured
//    with `shared/brand/contrast.ts` against the web vault's own grounds, in both themes.
// 3. Every icon's four tiles are the mark's, in the palette's colours, and the .ico is the
//    32 pixel PNG.
// 4. Every file the compose file mounts from `branding/` exists, and `branding.env` is loaded.
// 5. The mail templates carry no image and link only to the server, say «password manager»,
//    and none of them says «Vaultwarden» (REB-699); the compose file stops the server
//    attaching its own inline images.
// 6. The hook that brands the web vault is mounted where the image's /start.sh sources it,
//    parses as POSIX sh, and cannot stop the server from starting (REB-699).
import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { inflateSync } from 'node:zlib'

import { AA_TEXT, contrastRatio } from '../../../shared/brand/contrast.ts'
import { BRAND_TILES } from '../../../shared/brand/mark.ts'
import { IMAGES, PAIRS, PRODUCT_NAME, resolvePair, token } from '../branding/build.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const project = join(here, '..')
let failures = 0
const fail = (message) => {
  console.error(`FAIL: ${message}`)
  failures++
}

const check = spawnSync(process.execPath, [join(project, 'branding', 'build.mjs'), '--check'], { stdio: 'inherit' })
if (check.status !== 0) fail('branding/ is out of step with shared/brand')

for (const [theme, pairs] of Object.entries(PAIRS)) {
  for (const pair of pairs) {
    const [fg, bg] = resolvePair(theme, pair)
    const ratio = contrastRatio(fg, bg)
    const line = `${theme.padEnd(5)} ${fg} on ${bg}  ${ratio.toFixed(2)}:1  ${pair[2]}`
    if (ratio < AA_TEXT) fail(`${line} is below ${AA_TEXT}:1`)
    else console.log(line)
  }
}

// The favicons are drawn in a browser and are not byte-stable, but what they are is: the
// four tiles of the mark in the order `shared/brand` names them, in the palette's colours.
// Read the 32 pixel one back and compare the middle of each tile.
function pixels(png) {
  let offset = 8
  let width = 0
  let bpp = 0
  const idat = []
  while (offset < png.length) {
    const length = png.readUInt32BE(offset)
    const type = png.toString('latin1', offset + 4, offset + 8)
    const body = png.subarray(offset + 8, offset + 8 + length)
    if (type === 'IHDR') {
      width = body.readUInt32BE(0)
      // 8 bits a channel, truecolour with or without alpha, not interlaced.
      if (body[8] !== 8 || (body[9] !== 2 && body[9] !== 6) || body[12] !== 0) throw new Error('expected an 8-bit RGB or RGBA PNG that is not interlaced')
      bpp = body[9] === 6 ? 4 : 3
    }
    if (type === 'IDAT') idat.push(body)
    offset += 12 + length
  }
  const raw = inflateSync(Buffer.concat(idat))
  const stride = width * bpp
  const out = Buffer.alloc(stride * width)
  for (let y = 0; y < width; y++) {
    const filter = raw[y * (stride + 1)]
    for (let x = 0; x < stride; x++) {
      const value = raw[y * (stride + 1) + 1 + x]
      const left = x >= bpp ? out[y * stride + x - bpp] : 0
      const up = y > 0 ? out[(y - 1) * stride + x] : 0
      const upLeft = y > 0 && x >= bpp ? out[(y - 1) * stride + x - bpp] : 0
      const predictor = [0, left, up, (left + up) >> 1, paeth(left, up, upLeft)][filter]
      out[y * stride + x] = (value + predictor) & 255
    }
  }
  return { width, at: (x, y) => [...out.subarray(y * stride + x * bpp, y * stride + x * bpp + 3)] }
}
function paeth(a, b, c) {
  const p = a + b - c
  const [pa, pb, pc] = [Math.abs(p - a), Math.abs(p - b), Math.abs(p - c)]
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c
}
const hex = ([r, g, b]) => `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`
// Every generated icon, not only the 32 pixel one, and the .ico must carry that one.
for (const [file, size, pad, ground] of IMAGES) {
  const icon = pixels(readFileSync(join(project, 'branding', file)))
  // The mark occupies 700 of the 700 + 2 * pad units, so a tile's middle sits a quarter and
  // three quarters of the way across the 700.
  const at = (fraction) => Math.round(((pad + 700 * fraction) / (700 + 2 * pad)) * size)
  const tiles = [
    [at(0.25), at(0.25), BRAND_TILES[0]],
    [at(0.75), at(0.25), BRAND_TILES[1]],
    [at(0.25), at(0.75), BRAND_TILES[2]],
    [at(0.75), at(0.75), BRAND_TILES[3]],
  ]
  for (const [x, y, name] of tiles) {
    const want = token(name === 'ink' ? 'prussian-blue' : name)
    const got = hex(icon.at(x, y))
    if (got !== want) fail(`${file}: the ${name} tile is ${got}, shared/brand says ${want}`)
  }
  if (ground) {
    const got = hex(icon.at(1, 1))
    if (got !== token(ground)) fail(`${file}: the ground is ${got}, shared/brand says ${token(ground)}`)
  }
}
const ico = readFileSync(join(project, 'branding', 'images', 'favicon.ico'))
const png32 = readFileSync(join(project, 'branding', 'images', 'favicon-32x32.png'))
if (!ico.subarray(22).equals(png32)) fail('favicon.ico does not carry favicon-32x32.png')

for (const file of ['templates/scss/user.vaultwarden.scss.hbs', 'images/safari-pinned-tab.svg']) {
  if (readFileSync(join(project, 'branding', file), 'utf8').includes('undefined')) fail(`branding/${file} contains "undefined"`)
}

// The mails carry no image and no link away from the server they are about, and the compose
// file stops the server attaching its own. Every template of ours, the frame, the invitation
// and the six notices that replace upstream's bodies, is held to it.
const mailDir = join(project, 'branding', 'templates', 'email')
const mailFiles = readdirSync(mailDir).sort()
if (mailFiles.length !== 17) fail(`branding/templates/email holds ${mailFiles.length} files, expected 17 (frame, invitation, six notices)`)
for (const file of mailFiles) {
  const body = readFileSync(join(mailDir, file), 'utf8')
  if (/<img|<picture|<link|\bsrc\s*=|url\(|github|cid:/i.test(body)) fail(`branding/templates/email/${file} has an image, a stylesheet or a GitHub link`)
  const links = [...body.matchAll(/href\s*=\s*"([^"]*)"/gi)].map((m) => m[1]).filter((href) => !/^\{\{\{?url\}\}\}?(\/.*)?$/.test(href))
  if (links.length) fail(`branding/templates/email/${file} links to ${links.join(', ')}, not only to the page the mail is about`)
  if (/vaultwarden/i.test(body)) fail(`branding/templates/email/${file} says Vaultwarden to the person who reads it`)
}

const compose = readFileSync(join(project, 'docker-compose.yml'), 'utf8')
if (!/^\s+SMTP_EMBED_IMAGES: 'false'\s*$/m.test(compose)) fail("docker-compose.yml lets the server attach its inline images (SMTP_EMBED_IMAGES: 'false')")
if (!compose.includes(`SMTP_FROM_NAME: ${PRODUCT_NAME}\n`)) fail(`docker-compose.yml does not send the mails as «${PRODUCT_NAME}» (SMTP_FROM_NAME)`)
if (!compose.includes(`INVITATION_ORG_NAME: ${PRODUCT_NAME}\n`)) fail(`docker-compose.yml leaves the admin page's invitations and the confirmation mail naming Vaultwarden (INVITATION_ORG_NAME: ${PRODUCT_NAME})`)
// The server's own 404 page is ours too: no upstream name, no image, no link off the server.
const notFound = readFileSync(join(project, 'branding', 'templates', '404.hbs'), 'utf8')
if (/vaultwarden|<img|github|https?:/i.test(notFound)) fail('branding/templates/404.hbs names Vaultwarden, draws an image or links off the server')
if (!notFound.includes(PRODUCT_NAME)) fail(`branding/templates/404.hbs does not name the product «${PRODUCT_NAME}»`)
if (!/^\s+TEMPLATES_FOLDER: \/templates\s*$/m.test(compose)) fail('docker-compose.yml does not point TEMPLATES_FOLDER at the mounted /templates, so no override would load')

// Every mail says «password manager» where a reader looks first: the frame's head and
// footer, and the invitation's subject and body.
for (const file of mailFiles) {
  const body = readFileSync(join(mailDir, file), 'utf8')
  if (!/password manager/i.test(body)) fail(`branding/templates/email/${file} does not say «password manager»`)
}
if (!/>password manager<\/div>/.test(readFileSync(join(project, 'branding', 'templates', 'email', 'email_header.hbs'), 'utf8'))) fail('the mail header has no visible «password manager» label under the wordmark (the <title> does not count)')
const invite = readFileSync(join(project, 'branding', 'templates', 'email', 'send_org_invite.hbs'), 'utf8')
if (!/password manager/i.test(invite.split('\n')[0])) fail('the invitation subject does not name the password manager')

// The stylesheet: balanced, the product's lockup has no graft (four tiles, the box cut under
// the word), and the label is there.
const sheet = readFileSync(join(project, 'branding', 'templates', 'scss', 'user.vaultwarden.scss.hbs'), 'utf8')
if ((sheet.match(/\{/g) ?? []).length !== (sheet.match(/\}/g) ?? []).length) fail('the generated stylesheet has unbalanced braces')
if (!sheet.includes("content: 'password manager'")) fail('the stylesheet has no «password manager» label under the lockup')
const lockups = [...sheet.matchAll(/--rebase-lockup-(?:ink|paper): url\('data:image\/svg\+xml;base64,([^']+)'\)/g)].map((m) => Buffer.from(m[1], 'base64').toString())
if (lockups.length !== 2) fail(`the stylesheet carries ${lockups.length} lockups, expected 2`)
for (const svg of lockups) {
  if (!/viewBox="0 0 3943\.5 714"/.test(svg)) fail('a lockup in the stylesheet keeps the graft box')
  if ((svg.match(/<rect/g) ?? []).length !== 4) fail('a lockup in the stylesheet still draws the graft')
}
const mounted = [...compose.matchAll(/^\s*- (\.\/branding\/[^:]+):/gm)].map((m) => m[1])
if (mounted.length === 0) fail('docker-compose.yml mounts nothing from branding/')
if (!compose.includes('env_file: ./branding/branding.env')) fail('docker-compose.yml does not load branding/branding.env, so a new look would not recreate the container')
for (const path of mounted) {
  if (!existsSync(join(project, path))) fail(`docker-compose.yml mounts ${path}, which does not exist`)
}

// The hook (REB-699). The image's /start.sh sources /etc/vaultwarden.d/*.sh in its own shell
// and then execs the server, so the file is mounted there, parses as POSIX sh, reads only
// what branding.env carries, and has no top-level `exit`, `set` or `return` that would stop
// or change the start. It is how every «Vaultwarden Web» of the page becomes the product's
// name; the drill proves the served result.
if (!compose.includes('- ./scripts/brand-web-vault.sh:/etc/vaultwarden.d/10-rebase-web-vault.sh:ro')) fail('docker-compose.yml does not mount scripts/brand-web-vault.sh into /etc/vaultwarden.d/')
if (!compose.includes('- ./branding/images:/branding/images:ro')) fail('docker-compose.yml does not mount branding/images where the hook reads it')
const hookPath = join(project, 'scripts', 'brand-web-vault.sh')
const hook = readFileSync(hookPath, 'utf8')
const parse = spawnSync('sh', ['-n', hookPath], { encoding: 'utf8' })
if (parse.status !== 0) fail(`scripts/brand-web-vault.sh does not parse as sh: ${parse.stderr}`)
for (const line of hook.split('\n')) {
  if (/^(exit|set|return|trap|cd)\b/.test(line)) fail(`scripts/brand-web-vault.sh runs «${line}» at the top level of the shell that starts the server`)
}
const env = readFileSync(join(project, 'branding', 'branding.env'), 'utf8')
for (const variable of ['BRAND_NAME', 'BRAND_THEME_COLOR', 'BRAND_BACKGROUND_COLOR']) {
  if (!hook.includes(variable)) fail(`scripts/brand-web-vault.sh does not read ${variable}`)
  if (!new RegExp(`^${variable}=.+$`, 'm').test(env)) fail(`branding/branding.env does not carry ${variable}`)
}
if (!env.includes(`BRAND_NAME=${PRODUCT_NAME}\n`)) fail(`branding/branding.env does not name the product «${PRODUCT_NAME}»`)

if (failures) process.exit(1)
console.log(`branding: ${mounted.length} mounts present, every pair at or above ${AA_TEXT}:1`)
