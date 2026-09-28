import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// The privacy page's section on the team builder (REB-515, REB-516): what it must say
// about Anthropic, and what it must not claim. The seam asks the API for global
// inference, so the page makes no claim about where the processing happens.
const html = readFileSync(resolve(__dirname, 'privacy.html'), 'utf8')

function section(title: string): string {
  const start = html.indexOf(title)
  expect(start, `the section «${title}» exists`).toBeGreaterThan(-1)
  const rest = html.slice(start + title.length)
  const next = rest.search(/<h2\b/)
  return next === -1 ? rest : rest.slice(0, next)
}

describe('the privacy page on the team builder', () => {
  const text = section('La tua scheda e il team builder')

  it('names Anthropic as the processor and says the data trains no model', () => {
    expect(text).toContain('Anthropic tratta questi dati per conto nostro')
    expect(text).toContain('non li usa per addestrare i propri modelli')
    expect(text).toContain('https://www.anthropic.com/privacy')
  })

  it('says what leaves: the CV text and the visitor description, never the rate', () => {
    expect(text).toContain('il testo del tuo CV')
    expect(text).toContain('mai la tariffa stessa')
  })

  it('makes no claim about where the inference happens', () => {
    expect(text).not.toContain('Unione Europea')
    expect(text).not.toContain('Spazio economico europeo')
    expect(text).not.toMatch(/inferenza/i)
  })
})
