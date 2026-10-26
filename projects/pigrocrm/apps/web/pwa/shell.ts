/**
 * What the service worker precaches, worked out from the build and nothing else
 * (REB-731). Pure, so `pwa.test.ts` holds it to the rule without running a build.
 *
 * The "shell" here is the offline page and what it draws itself with: its script, its
 * stylesheets, the font those name. It is deliberately not the application: the SPA's
 * own chunks are fetched by the browser as they always were, and no document and no API
 * answer is ever stored (`sw/index.ts` has the rule).
 */
import { createHash } from 'node:crypto'

/** The URL a reference points at, when it is under `base`: query and fragment dropped. */
function underBase(reference: string, base: string): string | null {
  if (!reference.startsWith(base)) return null
  const end = reference.search(/[?#]/)
  return end === -1 ? reference : reference.slice(0, end)
}

/** Every `href` and `src` of an HTML document, and every `url(...)` of a stylesheet,
 *  that lives under `base`, in first-seen order and without repeats. */
export function referencedUrls(text: string, base: string): string[] {
  const found = new Set<string>()
  const patterns = [/\b(?:href|src)="([^"]+)"/g, /url\(\s*["']?([^"')\s]+)/g]
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const url = underBase(match[1] ?? '', base)
      if (url) found.add(url)
    }
  }
  return [...found]
}

/**
 * The files the shell is made of, as absolute paths: what the offline page links, then
 * what each stylesheet among those links. `source` answers with the text of an emitted
 * file, or `undefined` for one that is not a stylesheet this build wrote.
 */
export function shellUrls(
  offlineHtml: string,
  base: string,
  source: (url: string) => string | undefined,
): string[] {
  const urls = new Set(referencedUrls(offlineHtml, base))
  for (const url of [...urls]) {
    if (!url.endsWith('.css')) continue
    for (const nested of referencedUrls(source(url) ?? '', base)) urls.add(nested)
  }
  return [...urls]
}

/** Names this deploy's cache and changes the worker's own bytes: it hashes the offline
 *  page itself, which has no hash in its name, every file it needs, and the bytes of
 *  the ones among them that have none either (`mark.svg`, the touch icon). Without
 *  those, a redrawn icon would leave `sw.js` byte for byte what it was, no browser
 *  would look for a new worker, and the precache would keep the old picture. */
export function buildId(
  offlineHtml: string,
  urls: readonly string[],
  contents: ReadonlyMap<string, Uint8Array>,
): string {
  const hash = createHash('sha256').update(offlineHtml).update('\0')
  for (const url of [...urls].sort()) {
    hash.update(url).update('\0').update(contents.get(url) ?? '').update('\0')
  }
  return hash.digest('hex').slice(0, 12)
}
