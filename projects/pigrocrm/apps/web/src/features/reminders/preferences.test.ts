import { describe, expect, it } from 'vitest'
import { groupTypes } from './preferenceGroups'
import {
  resolveGeneral,
  resolveType,
  thresholdWording,
  visibleTypes,
  type PreferenceRow,
  type PreferenceType,
  type Preferences,
} from './preferences'

const KIND: PreferenceType = {
  key: 'invoice_overdue',
  label: 'Fattura scaduta',
  description: '',
  subject: 'invoice',
  audience: 'subject',
  direction: 'since',
  unit: 'days',
  ladder: { warning: 1, serious: 8, critical: 30 },
  daily: false,
}

function row(scope: 'space' | 'me', type: string | null, fields: Partial<PreferenceRow>): PreferenceRow {
  return {
    scope,
    type,
    enabled: null,
    channels: null,
    ladder: null,
    urgent_from: null,
    email_mode: null,
    quiet_hours: null,
    summary_hour: null,
    updated_at: '2026-10-26T09:00:00Z',
    ...fields,
  }
}

function data(space: PreferenceRow[], mine: PreferenceRow[]): Preferences {
  return {
    types: [KIND],
    space,
    mine,
    effective: [],
    general: { quiet_hours: { enabled: true, start: '20:00', end: '08:00', weekend: true }, summary_hour: 8 },
  }
}

describe('resolveType', () => {
  it('reads the built-in values where nothing is set', () => {
    const resolved = resolveType(KIND, 'me', data([], []))
    expect(resolved.channels).toEqual({ value: ['push', 'slack', 'email'], source: 'default' })
    expect(resolved.ladder).toEqual({ value: KIND.ladder, source: 'default' })
    expect(resolved.urgentFrom).toEqual({ value: 'critical', source: 'default' })
    expect(resolved.emailMode).toEqual({ value: null, source: 'default' })
    expect(resolved.enabled).toEqual({ value: true, source: 'default' })
  })

  it("takes the person's value over the space's, and the space's over the built-in one", () => {
    const space = [row('space', 'invoice_overdue', { channels: ['push'], urgent_from: 'serious' })]
    const mine = [row('me', 'invoice_overdue', { channels: ['push', 'email'] })]
    const resolved = resolveType(KIND, 'me', data(space, mine))
    expect(resolved.channels).toEqual({ value: ['push', 'email'], source: 'mine' })
    expect(resolved.urgentFrom).toEqual({ value: 'serious', source: 'space' })
  })

  it("shows an admin editing the space's defaults the space's values, not their own overrides", () => {
    const space = [row('space', 'invoice_overdue', { channels: ['push'] })]
    const mine = [row('me', 'invoice_overdue', { channels: ['email'] })]
    expect(resolveType(KIND, 'space', data(space, mine)).channels).toEqual({ value: ['push'], source: 'space' })
  })

  it('keeps a type the space switched off off for a person, whatever they chose', () => {
    const space = [row('space', 'invoice_overdue', { enabled: false })]
    const mine = [row('me', 'invoice_overdue', { enabled: true })]
    const resolved = resolveType(KIND, 'me', data(space, mine))
    expect(resolved.spaceOff).toBe(true)
    expect(resolved.enabled).toEqual({ value: false, source: 'space' })
  })

  it('lets a person switch a type off for themselves', () => {
    const mine = [row('me', 'invoice_overdue', { enabled: false })]
    const resolved = resolveType(KIND, 'me', data([], mine))
    expect(resolved.spaceOff).toBe(false)
    expect(resolved.enabled).toEqual({ value: false, source: 'mine' })
  })
})

describe('resolveGeneral', () => {
  it('reads the quiet window and the summary hour from the nearest layer that sets them', () => {
    const space = [row('space', null, { summary_hour: 7 })]
    const mine = [
      row('me', null, { quiet_hours: { enabled: false, start: '21:00', end: '07:00', weekend: false } }),
    ]
    const resolved = resolveGeneral('me', data(space, mine))
    expect(resolved.summaryHour).toEqual({ value: 7, source: 'space' })
    expect(resolved.quietHours.source).toBe('mine')
    expect(resolved.quietHours.value.enabled).toBe(false)
    expect(resolveGeneral('me', data([], [])).summaryHour).toEqual({ value: 8, source: 'default' })
  })
})

describe('visibleTypes', () => {
  const admins: PreferenceType = { ...KIND, key: 'job_late', audience: 'admins' }

  it('hides the space-wide types from a member and shows every type to an admin', () => {
    expect(visibleTypes([KIND, admins], 'me', false)).toEqual([KIND])
    expect(visibleTypes([KIND, admins], 'me', true)).toEqual([KIND, admins])
    expect(visibleTypes([KIND, admins], 'space', true)).toEqual([KIND, admins])
  })
})

describe('thresholdWording', () => {
  it('reads «da N giorni» for a ladder that climbs with time and «N giorni prima» for one that climbs as a date nears', () => {
    expect(thresholdWording({ direction: 'since', unit: 'days' })).toEqual({ before: 'da', after: 'giorni' })
    expect(thresholdWording({ direction: 'until', unit: 'days' })).toEqual({ before: '', after: 'giorni prima' })
    expect(thresholdWording({ direction: 'since', unit: 'hours' }).after).toBe('ore')
    expect(thresholdWording({ direction: 'since', unit: 'minutes' }).after).toBe('minuti')
    expect(thresholdWording({ direction: 'since', unit: 'percent' }).after).toBe('%')
  })
})

describe('groupTypes', () => {
  it('files each type under its section, in the table order, and drops the empty ones', () => {
    const types = ['job_late', 'invoice_overdue', 'attivita_due'].map((key) => ({ ...KIND, key }))
    expect(groupTypes(types).map((group) => [group.label, group.types.map((kind) => kind.key)])).toEqual([
      ['Attività e deal', ['attivita_due']],
      ['Fatture e fatturazione', ['invoice_overdue']],
      ['Spazio', ['job_late']],
    ])
  })

  it('never loses a type a card added without a row in the table', () => {
    const subject = { ...KIND, key: 'brand_new' }
    const admins: PreferenceType = { ...KIND, key: 'brand_new_space', audience: 'admins' }
    expect(groupTypes([subject, admins]).map((group) => [group.label, group.types.map((kind) => kind.key)])).toEqual([
      ['Spazio', ['brand_new_space']],
      ['Altri promemoria', ['brand_new']],
    ])
  })
})
