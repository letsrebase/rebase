import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Plugin } from 'vite'

/**
 * What a search engine or an AI assistant reads about the site, derived from the pages
 * themselves (REB-701): `llms.txt` and the home's FAQPage structured data. Nothing here
 * is written twice. A title, a description, a question or an answer changes in its page
 * and these follow on the next build, which is the REB-113 rule ("nothing that is not
 * true anywhere else on the site") made structural instead of remembered.
 */

const SRC = __dirname

/** The source of one page, read from `src/` so the dev server, the build and the unit
 *  tests all see the same text. */
export function pageSource(file: string): string {
  return readFileSync(join(SRC, file.replace(/^\//, '')), 'utf-8')
}

const ENTITIES: Readonly<Record<string, string>> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
}

/** Markup to the text a reader sees: no tags, entities decoded once, whitespace
 *  collapsed. A named entity outside `ENTITIES` fails the build, so a page that starts
 *  using `&rsquo;` gets it added here instead of shipping it verbatim. */
export function plainText(html: string): string {
  return html
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<[^>]*>/g, '')
    .replace(/&(#\d{1,6}|#x[0-9a-f]{1,5}|[a-z]+);/gi, (whole, code: string) => {
      if (code[1] === 'x' || code[1] === 'X') return String.fromCodePoint(parseInt(code.slice(2), 16))
      if (code.startsWith('#')) return String.fromCodePoint(parseInt(code.slice(1), 10))
      const named = ENTITIES[code.toLowerCase()]
      if (named === undefined) throw new Error(`site-text: no decoding for the entity ${whole}; add it to ENTITIES`)
      return named
    })
    .replace(/\s+/g, ' ')
    .trim()
}

/** A page's `<title>` and `<meta name="description">`, the two lines a search result
 *  shows. Throws when either is missing: every indexable page has both, and a page that
 *  lost one should fail the build rather than ship an empty line. */
export function pageSummary(html: string, file = 'page'): { title: string; description: string } {
  const title = html.match(/<title>([\s\S]*?)<\/title>/)?.[1]
  const description = html.match(/<meta\s+name="description"\s+content="([^"]*)"/)?.[1]
  if (title === undefined || description === undefined) {
    throw new Error(`site-text: ${file} has no <title> or no <meta name="description">`)
  }
  return { title: plainText(title), description: plainText(description) }
}

/** Markup to Markdown text: each link becomes `[label](href)`, a relative href made
 *  absolute on `host`, and the whole goes through `plainText` once. For llms.txt, where
 *  a link in an answer («su GitHub») is half of what the answer says. */
export function markdownText(html: string, host: string): string {
  return plainText(
    html.replace(/<a\s[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g, (_whole, href: string, label: string) => {
      const target = href.startsWith('/') ? `${host}${href}` : href
      return `[${label}](${target})`
    }),
  )
}

export type Question = { question: string; answerHtml: string }

/** The questions and answers of the home's «Le domande» section (REB-605): every `<h3>`
 *  inside `.faq` and the `<p>` that follows it, the answer kept as markup so each
 *  reader can render its link its own way. Throws on a heading without an answer, so a
 *  restructured section fails loudly instead of shipping half a FAQ. */
export function faq(html: string): Question[] {
  const section = html.match(/<div class="grid-2 faq">([\s\S]*?)<\/section>/)?.[1]
  if (section === undefined) return []
  const items: Question[] = []
  for (const block of section.matchAll(/<h3>([\s\S]*?)<\/h3>\s*(?:<p>([\s\S]*?)<\/p>)?/g)) {
    const question = plainText(block[1] ?? '')
    if (block[2] === undefined) throw new Error(`site-text: the question «${question}» has no answer after it`)
    items.push({ question, answerHtml: block[2] })
  }
  return items
}

/** schema.org FAQPage for the questions above, as one JSON-LD document. Every `<` is
 *  escaped, so a decoded `&lt;/script&gt;` in an answer cannot close the script tag the
 *  document is written into. */
export function faqPageJsonLd(questions: Question[]): string {
  return JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: questions.map(({ question, answerHtml }) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: { '@type': 'Answer', text: plainText(answerHtml) },
    })),
  }).replace(/</g, '\\u003c')
}

/** The doors into the hub the home offers as buttons (`a.cta` to `/hub/...`), each path
 *  once with the first label the page gives it and its query string dropped. Attributes
 *  are read in any order. */
export function hubDoors(html: string): { label: string; path: string }[] {
  const doors = new Map<string, string>()
  for (const [, attributes, label] of html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)) {
    if (!/\bclass="(?:[^"]*\s)?cta[\s"]/.test(attributes!)) continue
    const path = attributes!.match(/\bhref="(\/hub\/[^"?#]*)/)?.[1]
    if (path !== undefined && !doors.has(path)) doors.set(path, plainText(label!))
  }
  return [...doors].map(([path, label]) => ({ path, label }))
}

/**
 * `/llms.txt` (llmstxt.org): the site in the shape an AI assistant reads first. The
 * name, the home's description as the summary and its Open Graph line as the paragraph,
 * then every indexable page with its own title and description, the doors into the hub,
 * and the home's questions with their answers. In Italian, like the pages it repeats.
 */
export function llmsTxt(host: string, indexable: Readonly<Record<string, string>>): string {
  const home = pageSource('index.html')
  const summary = pageSummary(home, 'index.html')
  const intro = plainText(home.match(/<meta\s+property="og:description"\s+content="([^"]*)"/)?.[1] ?? '')
  const pages = Object.entries(indexable).map(([path, file]) => {
    const { title, description } = pageSummary(pageSource(file), file)
    return `- [${title}](${host}${path}): ${description}`
  })
  const doors = hubDoors(home).map(({ label, path }) => `- [${label}](${host}${path})`)
  const questions = faq(home).map(({ question, answerHtml }) => `### ${question}\n\n${markdownText(answerHtml, host)}`)
  // An empty heading is a silent failure: a restructured page fails the build instead.
  if (doors.length === 0) throw new Error('site-text: index.html offers no a.cta into /hub/ for llms.txt')
  if (questions.length === 0) throw new Error('site-text: index.html has no «Le domande» for llms.txt')
  return [
    '# rebase',
    `> ${summary.description}`,
    intro,
    '## Pagine',
    pages.join('\n'),
    '## Per entrare',
    doors.join('\n'),
    '## Le domande',
    questions.join('\n\n'),
  ]
    .filter((part) => part !== '')
    .join('\n\n')
    .concat('\n')
}

/** Adds the FAQPage block to the home's head at build and in the dev server, from the
 *  same markup the visitor reads. Every other page passes through untouched. */
export function structuredDataPlugin(): Plugin {
  return {
    name: 'website-structured-data',
    transformIndexHtml(html, ctx) {
      // `ctx.path` is the page's path under `root` in the build and in the dev server
      // alike, so a future `src/x/index.html` is not mistaken for the home.
      if (ctx.path !== '/index.html') return html
      const questions = faq(html)
      if (questions.length === 0) throw new Error('site-text: index.html has no «Le domande» section to describe')
      const block = `    <script type="application/ld+json">${faqPageJsonLd(questions)}</script>\n  </head>`
      // A function, so a `$&` in an answer is text, not a replacement pattern.
      return html.replace('</head>', () => block)
    },
  }
}
