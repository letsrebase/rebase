import { describe, expect, it } from 'vitest'
import type { ContactPoint } from './api'
import {
  allValid,
  blankDraft,
  draftsFrom,
  extraPoints,
  loginPoint,
  pointSuffix,
  problemOf,
  toWrite,
} from './contactPoints'

const LOGIN: ContactPoint = {
  id: null,
  kind: 'email',
  value: 'ada@studio.it',
  label: 'work',
  note: null,
  is_primary: true,
  position: 0,
}
const PHONE: ContactPoint = {
  id: 'p1',
  kind: 'phone',
  value: '+39 333 123 4567',
  label: 'personal',
  note: 'solo WhatsApp',
  is_primary: true,
  position: 1,
}

describe('the contact points of a person', () => {
  it('keeps the sign-in address out of what is edited and out of what is sent', () => {
    const drafts = draftsFrom([LOGIN, PHONE])
    expect(drafts).toHaveLength(1)
    expect(loginPoint([LOGIN, PHONE])).toEqual(LOGIN)
    expect(extraPoints([LOGIN, PHONE])).toEqual([PHONE])
    expect(toWrite(drafts)).toEqual([
      { kind: 'phone', value: '+39 333 123 4567', label: 'personal', note: 'solo WhatsApp', is_primary: true },
    ])
  })

  it('drops a row left blank and trims the rest, an empty note being no note', () => {
    const typed = { ...blankDraft('email'), value: '  altra@studio.it ', note: '   ' }
    expect(toWrite([blankDraft(), typed])).toEqual([
      { kind: 'email', value: 'altra@studio.it', label: 'work', note: null, is_primary: false },
    ])
  })

  it.each([
    ['email', 'non-un-indirizzo', 'Serve un indirizzo email valido.'],
    ['phone', '12 34', 'Serve un numero di telefono, di almeno 6 cifre.'],
    ['linkedin', 'https://example.com/in/ada', 'Serve l’indirizzo di un profilo LinkedIn.'],
  ] as const)('says what is wrong with a %s typed as «%s»', (kind, value, problem) => {
    expect(problemOf({ ...blankDraft(kind), value })).toBe(problem)
  })

  it('accepts what each kind should hold and an empty row', () => {
    const rows = [
      { ...blankDraft('email'), value: 'ada@studio.it' },
      { ...blankDraft('phone'), value: '02 5550199' },
      { ...blankDraft('linkedin'), value: 'linkedin.com/in/ada' },
      blankDraft(),
    ]
    expect(allValid(rows)).toBe(true)
  })

  it('names a point by its label and note, and the sign-in address by nothing', () => {
    expect(pointSuffix(PHONE)).toBe(' (personale: solo WhatsApp)')
    expect(pointSuffix({ ...PHONE, label: 'billing', note: null })).toBe(' (amministrazione)')
    expect(pointSuffix(LOGIN)).toBe('')
  })
})
