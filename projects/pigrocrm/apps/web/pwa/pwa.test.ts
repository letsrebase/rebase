import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { buildManifest } from './manifest'
import { buildId, referencedUrls, shellUrls } from './shell'

const HTML = `<!doctype html><html><head>
<link rel="icon" href="/app/mark.svg" />
<script type="module" crossorigin src="/app/assets/offline-AAA.js"></script>
<link rel="modulepreload" crossorigin href="/app/assets/tokens-BBB.js?v=1">
<link rel="stylesheet" href="/app/assets/tokens-CCC.css#x">
<link rel="canonical" href="https://elsewhere.example/app/assets/not-ours.js">
<link rel="stylesheet" href="/other/base.css">
</head></html>`

describe('referencedUrls', () => {
  it('keeps what is under the base, without query or fragment, once each', () => {
    expect(referencedUrls(HTML + HTML, '/app/')).toEqual([
      '/app/mark.svg',
      '/app/assets/offline-AAA.js',
      '/app/assets/tokens-BBB.js',
      '/app/assets/tokens-CCC.css',
    ])
  })

  it('reads url() out of a stylesheet, quoted or not', () => {
    const css = `@font-face{src:url(/app/assets/f-1.woff2) format("woff2")}
      .a{background:url("/app/assets/b.svg")}.c{background:url(data:image/png;base64,AAAA)}`
    expect(referencedUrls(css, '/app/')).toEqual(['/app/assets/f-1.woff2', '/app/assets/b.svg'])
  })
})

describe('shellUrls', () => {
  it('adds what the stylesheets the page links draw with, and only those', () => {
    const css = '@font-face{src:url(/app/assets/outfit-1.woff2)}'
    const seen: string[] = []
    const urls = shellUrls(HTML, '/app/', (url) => {
      seen.push(url)
      return url.endsWith('.css') ? css : undefined
    })
    expect(urls).toContain('/app/assets/outfit-1.woff2')
    expect(urls).toContain('/app/assets/offline-AAA.js')
    expect(seen).toEqual(['/app/assets/tokens-CCC.css'])
  })
})

describe('buildId', () => {
  const urls = ['/app/assets/a.js', '/app/assets/b.css', '/app/mark.svg']
  const bytes = (mark: string) =>
    new Map([['/app/mark.svg', new TextEncoder().encode(mark)]])

  it('does not depend on the order the files were found in', () => {
    expect(buildId('<html>', urls, bytes('m'))).toBe(buildId('<html>', [...urls].reverse(), bytes('m')))
  })

  it('changes with the offline page, which has no hash in its name, and with the files', () => {
    const base = buildId('<html>', urls, bytes('m'))
    expect(buildId('<html> ', urls, bytes('m'))).not.toBe(base)
    expect(buildId('<html>', [...urls, '/app/assets/c.js'], bytes('m'))).not.toBe(base)
  })

  it('changes when a file with no hash in its name is redrawn, so the worker is replaced', () => {
    expect(buildId('<html>', urls, bytes('one'))).not.toBe(buildId('<html>', urls, bytes('two')))
  })
})

describe('buildManifest', () => {
  const manifest = buildManifest({
    ground: '#f1f2f3',
    icons: [{ src: '/app/assets/icons/i.png', sizes: '192x192', type: 'image/png', purpose: 'any' }],
  })

  it('scopes the app to the directory it is fetched from, so each space is its own app', () => {
    expect(manifest.start_url).toBe('./')
    // `/<slug>/app` from `/<slug>/app/manifest.webmanifest`: no trailing slash, so the
    // router's home `/<slug>/app?tab=...` is inside it, and `start_url` still is.
    expect(manifest.scope).toBe('../app')
    // A relative `id` would resolve against the origin and be the same for every space.
    expect(manifest).not.toHaveProperty('id')
  })

  it('is installable: a name, standalone, and the ground for the splash and the bar', () => {
    expect(manifest).toMatchObject({
      name: 'PigroCRM',
      display: 'standalone',
      background_color: '#f1f2f3',
      theme_color: '#f1f2f3',
    })
  })
})

describe('deploy/nginx/spa.conf', () => {
  const conf = readFileSync(
    path.join(__dirname, '..', '..', '..', 'deploy', 'nginx', 'spa.conf'),
    'utf-8',
  )

  // The three files a space is installed from, and the paths they must answer at: the
  // root's own, and every slug's. The general `/<slug>/app/` block answers anything else
  // with the SPA shell, which is not a manifest and not a worker.
  const files = ['sw.js', 'manifest.webmanifest', 'offline.html']

  /** Every `location` block that names `needle` on its first line, up to its closing brace. */
  const blocksFor = (needle: string): string[] =>
    conf
      .split(/\n(?=\s*location )/)
      .map((chunk) => chunk.trimStart())
      .filter((chunk) => {
        const first = chunk.split('\n')[0] ?? ''
        return first.includes(needle) || first.includes(needle.replace('.', '\\.'))
      })
      .map((chunk) => {
        // A one-line block ends on its own line; a longer one at the first `}` that
        // starts a line, since the slug pattern has a `{1,30}` of its own.
        if (/\}\s*$/.test(chunk.split('\n')[0] ?? '')) return chunk.split('\n')[0] ?? ''
        return chunk.slice(0, chunk.indexOf('\n    }') + 6)
      })

  // The worker's block captures the slug, which its `Service-Worker-Allowed` repeats.
  const slugBlock = (file: string) =>
    `location ~ "^${file === 'sw.js' ? '(/[a-z0-9][a-z0-9-]{1,30}[a-z0-9])' : '/[a-z0-9][a-z0-9-]{1,30}[a-z0-9]'}/app/${file.replace('.', '\\.')}$"`

  it.each(files)('serves %s at the root and under a slug, never the SPA shell', (file) => {
    expect(conf).toContain(`location = /app/${file}`)
    expect(conf).toContain(slugBlock(file))
    // First match wins among regexes: each file's block comes before the general one.
    const general = conf.indexOf('/app(/|$)" { try_files /app/index.html =404; }')
    expect(general).toBeGreaterThan(-1)
    expect(conf.indexOf(slugBlock(file))).toBeLessThan(general)
  })

  it('makes a browser ask again before using any of them, and repeats nosniff beside it', () => {
    // nginx drops the server-level add_header from a location that declares its own.
    for (const file of files) {
      const blocks = blocksFor(file)
      expect(blocks, file).toHaveLength(2)
      for (const block of blocks) {
        expect(block, file).toContain('add_header Cache-Control "no-cache" always;')
        expect(block, file).toContain('add_header X-Content-Type-Options nosniff always;')
      }
    }
  })

  it('lets the worker take the scope without a trailing slash, at the root and under every slug', () => {
    // A worker may only take a scope above its own directory when this header says so;
    // without it the registration is refused and nothing is installed (src/pwa/urls.ts).
    const [rootBlock, slugBlockText] = blocksFor('sw.js')
    expect(rootBlock).toContain('add_header Service-Worker-Allowed "/app" always;')
    expect(slugBlockText).toContain('add_header Service-Worker-Allowed "$1/app" always;')
  })

  it('names the manifest type itself, since this nginx has no mime line for it', () => {
    const blocks = blocksFor('manifest.webmanifest')
    expect(blocks).toHaveLength(2)
    for (const block of blocks) expect(block).toContain('default_type application/manifest+json;')
  })
})
