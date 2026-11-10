/**
 * REB-252: neither nginx layer in front of the CRM set `client_max_body_size`, so an
 * ordinary upload over nginx's own compiled-in 1 MiB default was answered with a bare
 * 413 by `web` (or, on the host, by the vhost) before the request ever reached `api`,
 * whatever the application itself allows -- `SIZE_MAX`
 * (`packages/core/src/pigrocrm/core/documents/schemas.py:51`, 100 MiB). This asserts
 * all three of the container's and the host's nginx configs carry the same shape: a
 * tight, explicit server-level default (so the limit is versioned rather than left to
 * whatever the `http` block it lands in happens to default to) and a
 * location matching `api/documents` (a regex with `(/|$)`, never a slashed proxied
 * prefix, which makes nginx redirect the bare path, REB-636) that clears the
 * application ceiling, so a
 * regression in either direction -- the directive dropped again, or set below the
 * ceiling it exists to match -- is caught here rather than by the next scanned
 * contract somebody uploads.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Mirrors DIMENSIONE_MAX in packages/core/src/pigrocrm/core/documents/schemas.py: kept
// in bytes here too, so a change to either side is a diff a reviewer can compare
// against the other rather than two numbers that silently drift apart.
const SIZE_MAX_BYTES = 100 * 1024 * 1024

const pigrocrmRoot = join(__dirname, '..', '..', '..', '..')

const configs: Record<string, string> = {
  'spa.conf (the web container)': readFileSync(
    join(pigrocrmRoot, 'deploy', 'nginx', 'spa.conf'),
    'utf-8',
  ),
  'pigro.letsrebase.conf (the production host)': readFileSync(
    join(pigrocrmRoot, 'deploy', 'nginx', 'pigro.letsrebase.conf'),
    'utf-8',
  ),
  'preview.pigro.letsrebase.conf (the preview host)': readFileSync(
    join(pigrocrmRoot, 'deploy', 'nginx', 'preview.pigro.letsrebase.conf'),
    'utf-8',
  ),
}

function bodySizeBytes(value: string): number {
  const match = value.match(/^(\d+(?:\.\d+)?)([kKmMgG]?)$/)
  if (!match) throw new Error(`unparseable client_max_body_size value: "${value}"`)
  const amount = match[1] ?? ''
  const unit = (match[2] ?? '').toLowerCase()
  const multiplier: Record<string, number> = { k: 1024, m: 1024 ** 2, g: 1024 ** 3 }
  return Number(amount) * (multiplier[unit] ?? 1)
}

describe.each(Object.entries(configs))('%s', (_label, conf) => {
  it('never proxies through a prefix location that ends with a slash (REB-636)', () => {
    // `location ^~ /api/x/ { proxy_pass ... }` makes nginx answer the bare `/api/x`
    // with a 301 to `/api/x/`, which FastAPI answers with a 307 back: a redirect loop
    // on every collection route, measured on the preview's `/api/legal-entities`. Every such
    // block under `/api/` is a regex with `(/|$)`. The catch-all `/api/` is the one
    // exception: the API has no route at `/api`, so its redirect ends in a 404 and never
    // loops. The SPA's own prefixes (`/app/`, `/`) are not API routes and may redirect.
    const blocks = [...conf.matchAll(/location\s+(?:\^~\s+)?(\/[^\s{"~]*)\s*\{([\s\S]*?)\n {4}\}/g)]
    const slashed = blocks
      .filter(
        ([, path = '', body = '']) =>
          path.startsWith('/api/') && path !== '/api/' && path.endsWith('/') && /proxy_pass/.test(body),
      )
      .map(([, path]) => path ?? '')
    expect(slashed).toEqual([])
  })

  it('sets a tight, explicit server-level default before the first location', () => {
    // Same slicing robots.test.ts uses for the same reason: nginx does not inherit a
    // directive into a location block that declares its own, so what matters is
    // whether every response not covered by a more specific block gets the tight
    // default, which only a server-level directive guarantees.
    const serverLevel = conf.slice(0, conf.indexOf('\n    location '))
    const matches = [...serverLevel.matchAll(/^\s*client_max_body_size\s+(\S+);/gm)]
    expect(matches, 'no server-level client_max_body_size before the first location').toHaveLength(1)
    const [directive] = matches
    if (!directive) throw new Error('unreachable: length assertion above already failed')
    expect(bodySizeBytes(directive[1] ?? '')).toBeLessThanOrEqual(1024 * 1024)
  })

  it('raises the limit for every /api/documents/ location to at least the application ceiling', () => {
    const blocks = [...conf.matchAll(/location [^\n]*api\/documents[^\n]*\{([\s\S]*?)\n {4}\}/g)]
    expect(blocks.length, 'no location matching api/documents').toBeGreaterThan(0)
    for (const block of blocks) {
      const directive = block[1]?.match(/^\s*client_max_body_size\s+(\S+);/m)
      expect(directive, `no client_max_body_size in ${block[0].slice(0, 60)}`).toBeTruthy()
      if (!directive) throw new Error('unreachable: truthy assertion above already failed')
      expect(bodySizeBytes(directive[1] ?? '')).toBeGreaterThanOrEqual(SIZE_MAX_BYTES)
    }
  })

  it('covers a space\'s own /<slug>/api/documents/ too, not only the bare path', () => {
    // A logged-in session always runs under its space's prefix (lib/tenant.ts prepends
    // it to every API call, and /app/ redirects there on the host vhosts), so a limit
    // that only covers the bare path covers no real upload at all.
    const block = conf.match(/location [^\n]*a-z0-9[^\n]*api\/documents[^\n]*\{([\s\S]*?)\n {4}\}/)
    expect(block, 'no location matching a slug-prefixed api/documents path').toBeTruthy()
    if (!block) throw new Error('unreachable: truthy assertion above already failed')
    const directive = block[1]?.match(/^\s*client_max_body_size\s+(\S+);/m)
    expect(directive, 'no client_max_body_size for the prefixed upload path').toBeTruthy()
    if (!directive) throw new Error('unreachable: truthy assertion above already failed')
    expect(bodySizeBytes(directive[1] ?? '')).toBeGreaterThanOrEqual(SIZE_MAX_BYTES)
  })

  it("raises the limit for the inbox's upload, bare and under a space's prefix (REB-971)", () => {
    // The inbox takes one request of up to 100 MiB of files (`UPLOAD_MAX_TOTAL_BYTES`), and
    // without its own location the upload would meet the 1m default and answer 413.
    const blocks = [...conf.matchAll(/location [^\n]*api\/inbox\/upload[^\n]*\{([\s\S]*?)\n {4}\}/g)]
    expect(blocks.length, 'no location matching api/inbox/upload').toBeGreaterThan(0)
    for (const block of blocks) {
      const directive = block[1]?.match(/^\s*client_max_body_size\s+(\S+);/m)
      expect(directive, `no client_max_body_size in ${block[0].slice(0, 60)}`).toBeTruthy()
      if (!directive) throw new Error('unreachable: truthy assertion above already failed')
      expect(bodySizeBytes(directive[1] ?? '')).toBeGreaterThanOrEqual(SIZE_MAX_BYTES)
    }
    const prefixed = blocks.filter((block) => /a-z0-9/.test(block[0].split('\n')[0] ?? ''))
    expect(prefixed.length, 'no slug-prefixed location for the inbox upload').toBeGreaterThan(0)
  })

  it("raises the limit for a bank statement's upload to the 10 MiB file plus its wrapper (REB-1025)", () => {
    // One CAMT.053 or XLSX of at most 10 MiB (`STATEMENT_SIZE_MAX`) in a multipart body: under the
    // 1m server default the upload would answer 413 before the API reads the role.
    const blocks = [...conf.matchAll(/location [^\n]*api\/bank-accounts\/[^\n]*statements\$[^\n]*\{([\s\S]*?)\n {4}\}/g)]
    expect(blocks.length, 'no location matching the statement upload').toBeGreaterThan(0)
    for (const block of blocks) {
      const directive = block[1]?.match(/^\s*client_max_body_size\s+(\S+);/m)
      expect(directive, `no client_max_body_size in ${block[0].slice(0, 60)}`).toBeTruthy()
      expect(bodySizeBytes(directive?.[1] ?? '')).toBeGreaterThanOrEqual(10 * 1024 * 1024)
    }
    const prefixed = blocks.filter((block) => /a-z0-9/.test(block[0].split('\n')[0] ?? ''))
    expect(prefixed.length, 'no slug-prefixed location for the statement upload').toBeGreaterThan(0)
  })

  it('gives the inbox classify routes a long read, bare and under a prefix (REB-972)', () => {
    // Reading a file is a call to the space's model that can take minutes, and a request reads up
    // to five: under nginx's 60 s default the person would get a 504 while the work went on.
    const blocks = [...conf.matchAll(/location [^\n]*api\/inbox\/\(classify[^\n]*\{([\s\S]*?)\n {4}\}/g)]
    expect(blocks.length, 'no location matching the inbox classify routes').toBeGreaterThan(0)
    for (const block of blocks) {
      const directive = block[1]?.match(/^\s*proxy_read_timeout\s+(\d+)s;/m)
      expect(directive, `no proxy_read_timeout in ${block[0].slice(0, 60)}`).toBeTruthy()
      expect(Number(directive?.[1])).toBeGreaterThanOrEqual(300)
    }
  })
})
