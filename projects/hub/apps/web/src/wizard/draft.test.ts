import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DRAFT_TTL_MS, clearDraft, loadDraft, saveDraft } from './draft'

interface Form {
  first_name: string
  email: string
  links: string[]
  cv: File | null
}

const KEY = 'rebase.wizard.test'

describe('the wizard draft', () => {
  beforeEach(() => window.localStorage.clear())
  afterEach(() => {
    window.localStorage.clear()
    vi.useRealTimers()
  })

  it('is nothing until something was saved', () => {
    expect(loadDraft<Form>(KEY)).toBeNull()
  })

  it('gives back the answers and the step they were at', () => {
    saveDraft<Form>(KEY, { first_name: 'Ada', email: 'ada@studio.it', links: ['https://a.dev'], cv: null }, 2)
    expect(loadDraft<Form>(KEY)).toEqual({
      value: { first_name: 'Ada', email: 'ada@studio.it', links: ['https://a.dev'], cv: null },
      index: 2,
    })
  })

  it('leaves a File out: it cannot be stored, and the person picks it again', () => {
    const cv = new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' })
    saveDraft<Form>(KEY, { first_name: 'Ada', email: '', links: [], cv }, 3)
    expect(loadDraft<Form>(KEY)?.value).toEqual({ first_name: 'Ada', email: '', links: [] })
  })

  it('forgets a draft older than its life', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-15T20:00:00Z'))
    saveDraft<Form>(KEY, { first_name: 'Ada', email: '', links: [], cv: null }, 1)
    vi.setSystemTime(new Date('2026-09-15T20:00:00Z').getTime() + DRAFT_TTL_MS + 1)
    expect(loadDraft<Form>(KEY)).toBeNull()
    // And it is gone from storage too, not only refused.
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('is empty after a blank draft: nothing typed is nothing to keep', () => {
    saveDraft<Form>(KEY, { first_name: '', email: '', links: [], cv: null }, 0)
    expect(loadDraft<Form>(KEY)).toBeNull()
  })

  it('ignores what it cannot read', () => {
    window.localStorage.setItem(KEY, '{not json')
    expect(loadDraft<Form>(KEY)).toBeNull()
    window.localStorage.setItem(KEY, JSON.stringify({ v: 99, value: { first_name: 'x' }, index: 1, savedAt: Date.now() }))
    expect(loadDraft<Form>(KEY)).toBeNull()
  })

  it('is cleared on purpose', () => {
    saveDraft<Form>(KEY, { first_name: 'Ada', email: '', links: [], cv: null }, 1)
    clearDraft(KEY)
    expect(loadDraft<Form>(KEY)).toBeNull()
  })

  it('survives a storage that refuses', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => saveDraft<Form>(KEY, { first_name: 'Ada', email: '', links: [], cv: null }, 1)).not.toThrow()
    setItem.mockRestore()
  })
})

describe('a draft saved before the 2026-11 English rename', () => {
  const OLD_KEY = 'rebase.wizard.azienda'
  const stored = (value: Record<string, unknown>) =>
    JSON.stringify({ v: 1, value, index: 2, savedAt: Date.now() })
  const RENAMES = {
    known: ['company_name', 'remote', 'email'],
    fields: { 'nome_azienda': 'company_name', 'remoto': 'remote' },
  }

  beforeEach(() => window.localStorage.clear())
  afterEach(() => {
    window.localStorage.clear()
    vi.useRealTimers()
  })

  it('is read once from the old key, moved to the new one, and the old key is dropped', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-20T10:00:00Z'))
    window.localStorage.setItem(OLD_KEY, stored({ company_name: 'ACME' }))
    expect(loadDraft<Form>(KEY, OLD_KEY)).toEqual({ value: { company_name: 'ACME' }, index: 2 })
    expect(window.localStorage.getItem(OLD_KEY)).toBeNull()
    expect(window.localStorage.getItem(KEY)).not.toBeNull()
    expect(loadDraft<Form>(KEY, OLD_KEY)?.index).toBe(2)
  })

  it('is not read any more after 30 days (2026-12-03), and the old key goes', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-12-03T00:00:01Z'))
    window.localStorage.setItem(OLD_KEY, stored({ company_name: 'ACME' }))
    expect(loadDraft<Form>(KEY, OLD_KEY)).toBeNull()
    expect(window.localStorage.getItem(OLD_KEY)).toBeNull()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('clearDraft takes both keys', () => {
    window.localStorage.setItem(OLD_KEY, stored({ company_name: 'ACME' }))
    window.localStorage.setItem(KEY, stored({ company_name: 'ACME' }))
    clearDraft(KEY, OLD_KEY)
    expect(window.localStorage.getItem(OLD_KEY)).toBeNull()
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('gives the Italian answers their English names, translates the remote option and drops the unknown', () => {
    window.localStorage.setItem(KEY, stored({ 'nome_azienda': 'ACME', 'remoto': 'in_sede', email: 'a@b.it', stranger: 'x' }))
    expect(loadDraft<Form>(KEY, undefined, RENAMES)).toEqual({
      value: { company_name: 'ACME', remote: 'on_site', email: 'a@b.it' },
      index: 2,
    })
  })
})
