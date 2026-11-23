import { describe, expect, it } from 'vitest'
import type { WorkProfileAdmin } from './api'
import { cloudWorkProfile, cutSummary } from './cloudProfile'

const read = (over: Partial<WorkProfileAdmin['profile']>, hits: WorkProfileAdmin['guard_hits'] = []) =>
  ({
    guard_hits: hits,
    profile: {
      summary: 'Un riassunto.',
      domains: [
        { where: 'domains[0].note', label: 'Fintech', products: 4, sessions: 9, note: 'privata' },
        { where: 'domains[1].note', label: 'Acme Corp', products: 1, sessions: 2, note: null },
      ],
      projects: [
        { id: 'p1', domain: 'Pagamenti' },
        { id: 'p2', domain: 'Pagamenti' },
        { id: 'p3', domain: null },
        { id: 'p4', domain: 'Cliente Rossi' },
      ],
      ...over,
    },
  }) as unknown as WorkProfileAdmin

describe('cloudWorkProfile: what a company reads, as core derives it', () => {
  it('keeps the summary, the domains with their counts and each project domain once, and nothing else', () => {
    expect(cloudWorkProfile(read({}))).toEqual({
      summary: 'Un riassunto.',
      domains: [
        { label: 'Fintech', products: 4, sessions: 9 },
        { label: 'Acme Corp', products: 1, sessions: 2 },
      ],
      project_domains: ['Pagamenti', 'Cliente Rossi'],
    })
  })

  it('leaves out a field an anonymity check named', () => {
    const hits = [
      { field: 'summary', rule: 'email' },
      { field: 'domains[1].label', rule: 'name_account_site_or_client' },
      { field: 'p4.domain', rule: 'name_account_site_or_client' },
    ]
    expect(cloudWorkProfile(read({}, hits))).toEqual({
      summary: null,
      domains: [{ label: 'Fintech', products: 4, sessions: 9 }],
      project_domains: ['Pagamenti'],
    })
  })

  it('cuts a long summary at a word within 600 characters, with an ellipsis', () => {
    const long = `${'parola '.repeat(120)}fine.`
    const cut = cloudWorkProfile(read({ summary: long })).summary ?? ''
    expect(cut.length).toBeLessThanOrEqual(601)
    expect(cut.endsWith('parola…')).toBe(true)
    expect(cutSummary('breve')).toBe('breve')
  })
})
