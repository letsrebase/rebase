import { describe, expect, it } from 'vitest'
import {
  KIND_LABELS,
  PICKABLE_KINDS,
  formatSize,
  isUnread,
  kindWord,
  needsCompany,
  visibilityLine,
  whyLine,
} from './labels'
import { validateInboxSearch } from './search'
import { ME, item } from './testing'

describe('what an item says about itself', () => {
  it('is unread only while it is new, has no kind and still holds its file', () => {
    expect(isUnread(item())).toBe(true)
    expect(isUnread(item({ kind: 'receipt' }))).toBe(false)
    expect(isUnread(item({ state: 'discarded' }))).toBe(false)
    expect(isUnread(item({ has_file: false }))).toBe(false)
  })

  it('waits for a company only when it has a kind that carries one and none yet', () => {
    expect(needsCompany(item({ kind: 'supplier_invoice' }))).toBe(true)
    expect(needsCompany(item({ kind: 'supplier_invoice', legal_entity_id: 'le1' }))).toBe(false)
    expect(needsCompany(item({ kind: 'contract' }))).toBe(false)
    expect(needsCompany(item())).toBe(false)
    expect(needsCompany(item({ kind: 'receipt', state: 'proposed' }))).toBe(false)
  })

  it('puts the company to choose before the server’s sentence, then the sentence, then a default', () => {
    expect(whyLine(item({ kind: 'receipt', reason: 'Letto' }))).toBe('Azienda da scegliere')
    expect(whyLine(item({ kind: 'contract', reason: 'Tipo indicato da una persona' }))).toBe('Tipo indicato da una persona')
    expect(whyLine(item())).toBe('Aspetta di essere letto')
    expect(whyLine(item({ state: 'duplicate', duplicate_of_id: 'x' }))).toBe('Stesso file di uno già arrivato')
  })

  it('names the kind in words, and «Da leggere» for a file nobody has read', () => {
    expect(kindWord(item({ kind: 'bank_statement' }))).toBe('Estratto conto')
    expect(kindWord(item())).toBe('Da leggere')
    expect(kindWord(item({ state: 'discarded' }))).toBe('Non riconosciuto')
  })

  it('offers every kind but the archive to a person', () => {
    expect(PICKABLE_KINDS).not.toContain('archive')
    expect(PICKABLE_KINDS).toHaveLength(Object.keys(KIND_LABELS).length - 1)
  })

  it('says who sees an item with no company: the uploader reads the shared sentence, an administrator hers', () => {
    expect(visibilityLine(item(), ME, false)).toBe('Visibile a te e agli amministratori finché non ha un’azienda')
    expect(visibilityLine(item(), ME, true)).toBe('Visibile solo a te e agli amministratori')
    expect(visibilityLine(item({ uploaded_by_id: 'u2' }), ME, true)).toBe(
      'Visibile solo a chi l’ha caricato e agli amministratori',
    )
    expect(visibilityLine(item({ legal_entity_id: 'le1' }), ME, false)).toBeNull()
  })

  it('writes a size as Finder does', () => {
    expect(formatSize(512)).toBe('512 byte')
    expect(formatSize(12_345)).toBe('12,1 kB')
  })
})

describe('the URL of the page', () => {
  it('lands on «Da gestire» with nothing open for anything it does not know', () => {
    expect(validateInboxSearch({})).toEqual({ tab: 'pending' })
    expect(validateInboxSearch({ tab: 'nonsense', item: 3 })).toEqual({ tab: 'pending' })
  })

  it('keeps a tab and an open item', () => {
    expect(validateInboxSearch({ tab: 'set_aside', item: 'abc' })).toEqual({ tab: 'set_aside', item: 'abc' })
  })
})
