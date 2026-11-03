// Record a short video of a feature in use, for the Screenshots and video section of a
// pull request.
//
//     node docs/pr-screenshots/record.mjs steps.mjs demo-1-invoice-issue.mp4 \
//         --url http://localhost:5173/app/invoices [--viewport 1440x900] \
//         [--storage-state state.json] [--pause 800] [--keep-webm] [--no-sandbox]
//
// `steps.mjs` exports a default async function that receives the Playwright page and a
// `pause(ms)` helper, and does what a person would do: click, type, wait for the result.
// Chromium records the whole context with Playwright's own `recordVideo`, a cursor is
// drawn on the page so a click is visible where it lands, and ffmpeg turns the `.webm`
// Playwright writes into an H.264 `.mp4`, the one container a PR body plays inline in
// every browser. Read the file before uploading it: `open demo-1.mp4`.
//
// On macOS the recording runs under Seatbelt (`sandbox-exec`): the browser, the steps
// module and ffmpeg may write only to the output directory, the user's temp and cache
// directories and /dev, and may open network connections only to localhost, where the
// app under test runs. Reads are not confined. `--no-sandbox` opts out, and a platform
// without `sandbox-exec` says so and records unconfined.
//
// Playwright is not a dependency of this folder: it is resolved from a workspace package
// that declares `@playwright/test`, the same install the e2e tests use, so the browser
// is the one already on the machine.

import { spawnSync } from 'node:child_process'
import {
  copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, realpathSync, rmSync,
  statSync, writeFileSync,
} from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..', '..')
const PACKAGES_WITH_PLAYWRIGHT = ['projects/website', 'projects/pigrocrm/apps/web', 'shared/brand']

function usage(message) {
  if (message) console.error(`record.mjs: ${message}`)
  console.error(
    'usage: node docs/pr-screenshots/record.mjs <steps.mjs> <out.mp4> --url <url>' +
      ' [--viewport WxH] [--storage-state <file>] [--pause <ms>] [--keep-webm] [--no-sandbox]',
  )
  process.exit(2)
}

function parseArgs(argv) {
  const args = { viewport: '1440x900', pause: 800, keepWebm: false, noSandbox: false }
  const positional = []
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--url') args.url = argv[++i]
    else if (arg === '--viewport') args.viewport = argv[++i]
    else if (arg === '--storage-state') args.storageState = argv[++i]
    else if (arg === '--pause') args.pause = Number(argv[++i])
    else if (arg === '--keep-webm') args.keepWebm = true
    else if (arg === '--no-sandbox') args.noSandbox = true
    else if (arg.startsWith('--')) usage(`unknown option ${arg}`)
    else positional.push(arg)
  }
  if (positional.length !== 2) usage('expected <steps.mjs> <out.mp4>')
  if (!args.url) usage('--url is required')
  if (!positional[1].endsWith('.mp4')) usage('the output file must end in .mp4')
  const match = /^([1-9]\d*)x([1-9]\d*)$/.exec(args.viewport)
  if (!match) usage(`--viewport wants WxH, got ${args.viewport}`)
  if (!Number.isFinite(args.pause) || args.pause < 0) usage('--pause wants a number of milliseconds')
  return {
    ...args,
    steps: resolve(positional[0]),
    out: resolve(positional[1]),
    width: Number(match[1]),
    height: Number(match[2]),
  }
}

function loadPlaywright() {
  for (const pkg of PACKAGES_WITH_PLAYWRIGHT) {
    try {
      const require = createRequire(join(ROOT, pkg, 'package.json'))
      return require('@playwright/test')
    } catch {
      // the next package may have it
    }
  }
  console.error(
    `record.mjs: @playwright/test is not installed in any of ${PACKAGES_WITH_PLAYWRIGHT.join(', ')}.` +
      ' Run `pnpm install --frozen-lockfile --prefer-offline` at the repository root.',
  )
  process.exit(1)
}

// A cursor Playwright does not draw: a dot that follows the mouse and swells on a
// click, so a reviewer sees where each action landed. Injected before any script of
// the page runs, in a layer of its own, and never part of the product. The last
// position survives a navigation through sessionStorage, so the dot is still there
// on the next page before the mouse moves again; a page that rewrites its root
// loses the node, and the next move puts it back.
const CURSOR_SCRIPT = `(() => {
  const KEY = '__pr_video_cursor'
  const dot = document.createElement('div')
  dot.setAttribute('aria-hidden', 'true')
  dot.style.cssText = 'position:fixed;left:0;top:0;width:18px;height:18px;margin:-9px 0 0 -9px;' +
    'border-radius:50%;background:rgba(255,122,0,.85);border:2px solid #fff;' +
    'box-shadow:0 0 0 2px rgba(255,122,0,.35);pointer-events:none;z-index:2147483647;' +
    'transition:transform .12s ease;transform:scale(1);opacity:0'
  const place = (x, y) => {
    dot.style.opacity = '1'
    dot.style.left = x + 'px'
    dot.style.top = y + 'px'
  }
  const attach = () => {
    if (!dot.isConnected && document.documentElement) document.documentElement.appendChild(dot)
  }
  attach()
  document.addEventListener('DOMContentLoaded', attach)
  try {
    const last = sessionStorage.getItem(KEY)
    if (last) { const [x, y] = last.split(','); place(Number(x), Number(y)) }
  } catch {}
  document.addEventListener('mousemove', (e) => {
    attach()
    place(e.clientX, e.clientY)
    try { sessionStorage.setItem(KEY, e.clientX + ',' + e.clientY) } catch {}
  }, true)
  document.addEventListener('mousedown', () => { dot.style.transform = 'scale(1.6)' }, true)
  document.addEventListener('mouseup', () => { dot.style.transform = 'scale(1)' }, true)
})()`

async function record(args) {
  const { chromium } = loadPlaywright()
  const stepsModule = await import(pathToFileURL(args.steps).href)
  const steps = stepsModule.default
  if (typeof steps !== 'function') {
    console.error(`record.mjs: ${args.steps} must export a default async function (page, { pause })`)
    process.exit(2)
  }

  const videoDir = mkdtempSync(join(tmpdir(), 'pr-video-'))
  const partial = () => {
    const name = readdirSync(videoDir).find((entry) => entry.endsWith('.webm'))
    return name ? join(videoDir, name) : null
  }

  let failure = null
  let browser = null
  try {
    browser = await chromium.launch()
    const context = await browser.newContext({
      viewport: { width: args.width, height: args.height },
      deviceScaleFactor: 1,
      storageState: args.storageState,
      recordVideo: { dir: videoDir, size: { width: args.width, height: args.height } },
    })
    await context.addInitScript(CURSOR_SCRIPT)
    const page = await context.newPage()
    const pause = (ms = args.pause) => page.waitForTimeout(ms)
    try {
      await page.goto(args.url, { waitUntil: 'networkidle' })
      await page.mouse.move(args.width / 2, args.height / 2)
      await pause()
      await steps(page, { pause })
      await pause()
    } finally {
      // Closing the context is what flushes the video to disk, on success and on failure.
      await context.close()
    }
  } catch (error) {
    failure = error
  } finally {
    await browser?.close()
  }

  const webm = partial()
  if (failure) {
    const where = webm ? `The partial recording is at ${webm}` : 'Nothing was recorded'
    console.error(`record.mjs: the flow failed: ${failure.message}. ${where}`)
    if (!webm) rmSync(videoDir, { recursive: true, force: true })
    process.exit(1)
  }
  if (!webm) {
    console.error('record.mjs: Playwright wrote no video')
    rmSync(videoDir, { recursive: true, force: true })
    process.exit(1)
  }
  return webm
}

function toMp4(webm, out) {
  // yuv420p and even dimensions are what QuickTime, Safari and the GitHub player agree
  // on; faststart puts the index first so the player starts before the download ends.
  const result = spawnSync(
    'ffmpeg',
    [
      '-y', '-loglevel', 'error', '-i', webm,
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-pix_fmt', 'yuv420p',
      '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
      '-movflags', '+faststart', '-an', out,
    ],
    { stdio: 'inherit' },
  )
  if (result.error?.code === 'ENOENT') {
    console.error('record.mjs: ffmpeg is not installed (`brew install ffmpeg`). The .webm is at ' + webm)
    process.exit(1)
  }
  if (result.status !== 0) {
    console.error('record.mjs: ffmpeg failed. The .webm is at ' + webm)
    process.exit(result.status ?? 1)
  }
}

function describe(out) {
  const probe = spawnSync(
    'ffprobe',
    ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', out],
    { encoding: 'utf8' },
  )
  const seconds = Number(probe.stdout)
  const kb = Math.round(statSync(out).size / 1024)
  const duration = Number.isFinite(seconds) ? `${seconds.toFixed(1)} s` : 'unknown length'
  console.log(`${out}: ${duration}, ${kb} kB`)
}

// Seatbelt. The outer process writes a profile for this run and re-executes itself
// under `sandbox-exec -f`; the inner one, marked by the environment, records. Rules are
// last-match-wins: everything is allowed, then writes are denied except where the run
// needs them, then the network is denied except loopback and unix sockets (Playwright
// talks to Chromium over pipes; the app under test answers on localhost). `network*`
// with a `local ip` filter is not enough: an outbound socket to example.com matched it
// (measured 2026-09-16), so outbound is allowed on `remote ip` only.
const SANDBOXED = 'PR_VIDEO_SANDBOXED'

function sbPath(path) {
  return `"${path.replace(/["\\]/g, '\\$&')}"`
}

function darwinDir(name, fallback) {
  const result = spawnSync('getconf', [name], { encoding: 'utf8' })
  const value = result.status === 0 ? result.stdout.trim() : ''
  return value || fallback
}

function seatbeltProfile(args) {
  // `tmpdir()` is $TMPDIR, where this script and Playwright put their temp files, and it
  // is not always the getconf directory (CI runners, direnv, a sandboxed shell set it).
  const writable = [
    dirname(args.out),
    tmpdir(),
    darwinDir('DARWIN_USER_TEMP_DIR', tmpdir()),
    darwinDir('DARWIN_USER_CACHE_DIR', ''),
    '/dev',
  ]
  const resolved = [
    ...new Set(writable.filter((dir) => dir && existsSync(dir)).map((dir) => realpathSync(dir))),
  ]
  return [
    '(version 1)',
    '(allow default)',
    '(deny file-write*)',
    ...resolved.map((dir) => `(allow file-write* (subpath ${sbPath(dir)}))`),
    '(deny network*)',
    '(allow network-outbound (remote ip "localhost:*"))',
    '(allow network-inbound (local ip "localhost:*"))',
    '(allow network* (remote unix-socket))',
    '(allow network* (local unix-socket))',
    '',
  ].join('\n')
}

function runInSandbox(args) {
  if (process.platform !== 'darwin' || !existsSync('/usr/bin/sandbox-exec')) {
    console.error('record.mjs: no Seatbelt on this platform, recording unconfined')
    return null
  }
  const dir = mkdtempSync(join(tmpdir(), 'pr-video-profile-'))
  const profile = join(dir, 'record.sb')
  writeFileSync(profile, seatbeltProfile(args))
  console.error(
    `record.mjs: under Seatbelt: writes only to ${dirname(args.out)}, the temp and cache` +
      ' directories and /dev; network only to localhost (--no-sandbox opts out)',
  )
  // Ctrl-C reaches the whole process group: the inner run shuts Chromium down, and the
  // outer one has to survive long enough to remove the profile, then die the same way.
  const ignore = () => {}
  process.on('SIGINT', ignore)
  process.on('SIGTERM', ignore)
  let result
  try {
    result = spawnSync(
      '/usr/bin/sandbox-exec',
      ['-f', profile, process.execPath, ...process.execArgv, ...process.argv.slice(1)],
      { stdio: 'inherit', env: { ...process.env, [SANDBOXED]: '1' } },
    )
  } finally {
    rmSync(dir, { recursive: true, force: true })
    process.off('SIGINT', ignore)
    process.off('SIGTERM', ignore)
  }
  if (result.error) {
    console.error(`record.mjs: sandbox-exec failed to start: ${result.error.message}`)
    return 1
  }
  if (result.signal) process.kill(process.pid, result.signal)
  return result.status ?? 1
}

const args = parseArgs(process.argv.slice(2))
mkdirSync(dirname(args.out), { recursive: true })
if (process.env[SANDBOXED] !== '1') {
  if (args.noSandbox) console.error('record.mjs: --no-sandbox, recording unconfined')
  else {
    const status = runInSandbox(args)
    if (status !== null) process.exit(status)
  }
}
const webm = await record(args)
toMp4(webm, args.out)
if (args.keepWebm) {
  const kept = args.out.replace(/\.mp4$/, '.webm')
  copyFileSync(webm, kept)
  console.log(`${kept}: the raw recording, kept`)
}
rmSync(dirname(webm), { recursive: true, force: true })
describe(args.out)
