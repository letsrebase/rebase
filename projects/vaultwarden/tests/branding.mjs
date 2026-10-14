// What the look of the web vault (REB-688) must keep true, with no server and no browser:
//
//   node projects/vaultwarden/tests/branding.mjs
//
// 1. The committed files are what `shared/brand` produces now (branding/build.mjs --check),
//    so a palette or lockup change that nobody regenerated fails here.
// 2. Every colour pair the stylesheet creates is readable: text at WCAG AA (4.5:1), measured
//    with `shared/brand/contrast.ts` against the web vault's own grounds, in both themes.
// 3. Every file the compose file mounts from `branding/` exists.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { AA_TEXT, contrastRatio } from '../../../shared/brand/contrast.ts'
import { PAIRS, resolvePair } from '../branding/build.mjs'

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

const compose = readFileSync(join(project, 'docker-compose.yml'), 'utf8')
const mounted = [...compose.matchAll(/^\s*- (\.\/branding\/[^:]+):/gm)].map((m) => m[1])
if (mounted.length === 0) fail('docker-compose.yml mounts nothing from branding/')
for (const path of mounted) {
  if (!existsSync(join(project, path))) fail(`docker-compose.yml mounts ${path}, which does not exist`)
}

if (failures) process.exit(1)
console.log(`branding: ${mounted.length} mounts present, every pair at or above ${AA_TEXT}:1`)
