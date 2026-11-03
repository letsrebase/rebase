/**
 * The template picker offers a template only when its type can make a document, and every
 * type a template or a document can have reads in Italian.
 *
 * The server's rule is `DOCUMENT_TYPES` (`documents/templating.py`), the `DocumentType` literal of
 * `documents/schemas.py`; the types a template can carry are the `TemplateType` literal of
 * `templates/schemas.py`. This reads both, so a type added on one side without the other
 * fails here instead of offering a choice that can only be refused.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DOCUMENT_TYPE_LABELS, canMakeDocument } from '@/features/documents/queries'
import { TEMPLATE_TYPE_LABELS } from '@/features/settings/labels'

const CORE = join(__dirname, '..', '..', '..', '..', 'packages', 'core', 'src', 'pigrocrm', 'core')

function literal(file: string, name: string): string[] {
  const source = readFileSync(join(CORE, file), 'utf8')
  const found = new RegExp(`^${name} = Literal\\[\\n([^\\]]*)\\]`, 'm').exec(source)
  if (found === null) throw new Error(`${name} is not a Literal in ${file}`)
  return [...found[1]!.matchAll(/"([\w]+)"/g)].map((match) => match[1]!)
}

const DOCUMENT_TYPES = literal('documents/schemas.py', 'DocumentType')
const TEMPLATE_TYPES = literal('templates/schemas.py', 'TemplateType')

describe('what a template can make', () => {
  it('reads the core', () => {
    expect(DOCUMENT_TYPES).toContain('invoice_xml')
    expect(TEMPLATE_TYPES).toContain('payment_reminder')
  })

  it('is offered exactly when its type is one the server accepts for a document', () => {
    for (const type of TEMPLATE_TYPES) {
      expect(canMakeDocument(type), type).toBe(DOCUMENT_TYPES.includes(type))
    }
    expect(canMakeDocument('payment_reminder')).toBe(false)
    expect(canMakeDocument('email')).toBe(false)
    expect(canMakeDocument('offer')).toBe(true)
  })

  it('reads in Italian for every type a document or a template can have', () => {
    for (const type of new Set([...DOCUMENT_TYPES, ...TEMPLATE_TYPES])) {
      expect(DOCUMENT_TYPE_LABELS[type], type).toBeDefined()
      expect(DOCUMENT_TYPE_LABELS[type], type).not.toBe(type)
    }
    for (const type of TEMPLATE_TYPES) {
      expect(TEMPLATE_TYPE_LABELS[type], type).toBeDefined()
    }
    expect(DOCUMENT_TYPE_LABELS.payment_reminder).toBe('Sollecito')
  })
})
