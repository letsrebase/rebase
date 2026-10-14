// What the look of the web vault (REB-688) must keep true, with no server and no browser:
//
//   node projects/vaultwarden/tests/branding.mjs
//
// 1. The committed files are what `shared/brand` produces now (branding/build.mjs --check),
//    so a palette or lockup change that nobody regenerated fails here.
// 2. Every colour pair the stylesheet creates is readable: text at WCAG AA (4.5:1), measured
//    with `shared/brand/contrast.ts` against the web vault's own grounds, in both themes.
// 3. The favicon's four tiles are the mark's, in the palette's colours.
// 4. Every file the compose file mounts from `branding/` exists.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { inflateSync } from 'node:zlib'

import { AA_TEXT, contrastRatio } from '../../../shared/brand/contrast.ts'
import { BRAND_TILES } from '../../../shared/brand/mark.ts'
import { PAIRS, resolvePair, token } from '../branding/build.mjs'

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
const favicon = pixels(readFileSync(join(project, 'branding', 'images', 'favicon-32x32.png')))
const quarter = favicon.width / 4
const tiles = [
  [quarter, quarter, BRAND_TILES[0]],
  [3 * quarter, quarter, BRAND_TILES[1]],
  [quarter, 3 * quarter, BRAND_TILES[2]],
  [3 * quarter, 3 * quarter, BRAND_TILES[3]],
]
for (const [x, y, name] of tiles) {
  const want = token(name === 'ink' ? 'prussian-blue' : name)
  const got = hex(favicon.at(x, y))
  if (got !== want) fail(`favicon-32x32.png: the ${name} tile is ${got}, shared/brand says ${want}`)
}

for (const file of ['templates/scss/user.vaultwarden.scss.hbs', 'images/safari-pinned-tab.svg']) {
  if (readFileSync(join(project, 'branding', file), 'utf8').includes('undefined')) fail(`branding/${file} contains "undefined"`)
}

const compose = readFileSync(join(project, 'docker-compose.yml'), 'utf8')
const mounted = [...compose.matchAll(/^\s*- (\.\/branding\/[^:]+):/gm)].map((m) => m[1])
if (mounted.length === 0) fail('docker-compose.yml mounts nothing from branding/')
if (!compose.includes('env_file: ./branding/branding.env')) fail('docker-compose.yml does not load branding/branding.env, so a new look would not recreate the container')
for (const path of mounted) {
  if (!existsSync(join(project, path))) fail(`docker-compose.yml mounts ${path}, which does not exist`)
}

if (failures) process.exit(1)
console.log(`branding: ${mounted.length} mounts present, every pair at or above ${AA_TEXT}:1`)
