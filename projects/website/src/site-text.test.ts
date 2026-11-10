import { describe, expect, it } from 'vitest'
import { faq, markdownText, pageSource, plainText, structuredDataPlugin } from './site-text'

const home = pageSource('index.html')

/** The plugin's transformIndexHtml as a plain function, the way Vite calls it: `path`
 *  is the page's path under `src/`. */
function transform(html: string, path: string): string {
  const hook = structuredDataPlugin().transformIndexHtml
  if (typeof hook !== 'function') throw new Error('transformIndexHtml is not a plain function')
  return hook.call({} as never, html, { filename: `/x/src${path}`, path } as never) as string
}

function jsonLdBlocks(html: string): Record<string, unknown>[] {
  return [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map(
    (m) => JSON.parse(m[1]!) as Record<string, unknown>,
  )
}

describe('faq, read from the home\'s «Le domande»', () => {
  it('reads each heading with the paragraph after it, in order, inside the section only', () => {
    const page = `<h3>Fuori</h3><p>no</p>
      <div class="grid-2 faq">
        <div><h3>Prima?</h3><p>Sì.</p></div>
        <div><h3>Seconda &amp; ultima?</h3>
          <p>Vedi <a class="quiet-link" href="/privacy">la privacy</a>.</p></div>
      </div></div></section><h3>Dopo</h3><p>no</p>`
    expect(faq(page)).toEqual([
      { question: 'Prima?', answerHtml: 'Sì.' },
      { question: 'Seconda & ultima?', answerHtml: 'Vedi <a class="quiet-link" href="/privacy">la privacy</a>.' },
    ])
  })

  it('finds the home\'s questions from the first to the last', () => {
    const questions = faq(home).map((q) => q.question)
    expect(questions[0]).toBe('Quanto costa entrare?')
    expect(questions.at(-1)).toBe('Come vi contatto?')
  })

  it('refuses a question with no answer after it, rather than shipping half a FAQ', () => {
    const broken = '<div class="grid-2 faq"><div><h3>Quanto costa?</h3></div></div></section>'
    expect(() => faq(broken)).toThrow(/Quanto costa\?/)
  })
})

describe('structuredDataPlugin', () => {
  it('adds a FAQPage to the home whose answers are the visible ones, beside the hand-written block', () => {
    const blocks = jsonLdBlocks(transform(home, '/index.html'))
    expect(blocks).toHaveLength(2)
    const page = blocks.find((block) => block['@type'] === 'FAQPage')!
    const entries = page.mainEntity as { name: string; acceptedAnswer: { text: string } }[]
    expect(entries.map((e) => e.name)).toEqual(faq(home).map((q) => q.question))
    const code = entries.find((e) => e.name === 'Posso vedere il codice?')!
    expect(code.acceptedAnswer.text).toBe('Sì. PigroCRM è open source su GitHub, self-hosted quando vuoi.')
  })

  it('leaves every other page as it is, a nested index.html included', () => {
    const pigrocrm = pageSource('pigrocrm.html')
    expect(transform(pigrocrm, '/pigrocrm.html')).toBe(pigrocrm)
    expect(transform(pigrocrm, '/deck/index.html')).toBe(pigrocrm)
  })

  it('fails the build when the home has lost its questions', () => {
    expect(() => transform('<html><head></head><body></body></html>', '/index.html')).toThrow(/Le domande/)
  })

  it('writes an answer that looks like markup or a replacement pattern as text', () => {
    const page = `<html><head></head><body><div class="grid-2 faq"><div><h3>Q?</h3>
      <p>Costa $&amp; e &lt;/script&gt; niente.</p></div></div></section></body></html>`
    const out = transform(page, '/index.html')
    expect(out.match(/<\/script>/g)).toHaveLength(1)
    const [faqPage] = jsonLdBlocks(out) as { mainEntity: { acceptedAnswer: { text: string } }[] }[]
    expect(faqPage!.mainEntity[0]!.acceptedAnswer.text).toBe('Costa $& e </script> niente.')
  })
})

describe('plainText', () => {
  it('fails on a named entity it cannot decode instead of shipping it verbatim', () => {
    expect(() => plainText('l&rsquo;azienda')).toThrow(/&rsquo;/)
    expect(plainText('l&#39;azienda &amp;lt;')).toBe("l'azienda &lt;")
  })
})

describe('markdownText', () => {
  it('keeps a link as Markdown, absolute on the host, and decodes entities', () => {
    expect(markdownText('Vedi <a class="x" href="/privacy">la privacy</a> &amp; <a href="mailto:a@b.it">a@b.it</a>', 'https://h.it')).toBe(
      'Vedi [la privacy](https://h.it/privacy) & [a@b.it](mailto:a@b.it)',
    )
  })
})
