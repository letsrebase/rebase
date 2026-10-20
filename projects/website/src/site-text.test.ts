import { describe, expect, it } from 'vitest'
import { faq, markdownText, pageSource, plainText, structuredDataPlugin } from './site-text'

const home = pageSource('index.html')

/** The plugin's transformIndexHtml as a plain function, the way Vite calls it. */
function transform(html: string, filename: string): string {
  const hook = structuredDataPlugin().transformIndexHtml
  if (typeof hook !== 'function') throw new Error('transformIndexHtml is not a plain function')
  return hook.call({} as never, html, { filename, path: '/' } as never) as string
}

function jsonLdBlocks(html: string): Record<string, unknown>[] {
  return [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map(
    (m) => JSON.parse(m[1]!) as Record<string, unknown>,
  )
}

describe('faq, read from the home\'s «Le domande»', () => {
  it('finds every question the section shows, in order, each with its answer', () => {
    const section = home.match(/<div class="grid-2 faq">([\s\S]*?)<\/section>/)![1]!
    const headings = [...section.matchAll(/<h3>([\s\S]*?)<\/h3>/g)].map((m) => plainText(m[1]!))
    const questions = faq(home)
    expect(headings.length).toBeGreaterThanOrEqual(4)
    expect(questions.map((q) => q.question)).toEqual(headings)
    for (const { answerHtml } of questions) expect(plainText(answerHtml)).not.toBe('')
  })

  it('refuses a question with no answer after it, rather than shipping half a FAQ', () => {
    const broken = '<div class="grid-2 faq"><div><h3>Quanto costa?</h3></div></div></section>'
    expect(() => faq(broken)).toThrow(/Quanto costa\?/)
  })
})

describe('structuredDataPlugin', () => {
  it('adds a FAQPage to the home whose answers are the visible ones, beside the hand-written block', () => {
    const blocks = jsonLdBlocks(transform(home, '/x/src/index.html'))
    expect(blocks).toHaveLength(2)
    const page = blocks.find((block) => block['@type'] === 'FAQPage')!
    const entries = page.mainEntity as { name: string; acceptedAnswer: { text: string } }[]
    expect(entries.map((e) => e.name)).toEqual(faq(home).map((q) => q.question))
    const code = entries.find((e) => e.name === 'Posso vedere il codice?')!
    expect(code.acceptedAnswer.text).toBe('Sì. PigroCRM è open source su GitHub, self-hosted quando vuoi.')
  })

  it('leaves every other page as it is', () => {
    const pigrocrm = pageSource('pigrocrm.html')
    expect(transform(pigrocrm, '/x/src/pigrocrm.html')).toBe(pigrocrm)
  })

  it('fails the build when the home has lost its questions', () => {
    expect(() => transform('<html><head></head><body></body></html>', '/x/src/index.html')).toThrow(/Le domande/)
  })
})

describe('markdownText', () => {
  it('keeps a link as Markdown, absolute on the host, and decodes entities', () => {
    expect(markdownText('Vedi <a class="x" href="/privacy">la privacy</a> &amp; <a href="mailto:a@b.it">a@b.it</a>', 'https://h.it')).toBe(
      'Vedi [la privacy](https://h.it/privacy) & [a@b.it](mailto:a@b.it)',
    )
  })
})
