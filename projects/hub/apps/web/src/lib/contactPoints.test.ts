import { describe, expect, it } from 'vitest'
import type { ContactPoint } from './api'
import {
  allValid,
  blankDraft,
  contactsChanged,
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
    ['phone', '12 34', 'Serve un numero di telefono, di almeno 6 cifre e al massimo 40 caratteri.'],
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

  it('knows whether the list differs from the stored one, a blank row aside', () => {
    expect(contactsChanged(draftsFrom([LOGIN, PHONE]), [LOGIN, PHONE])).toBe(false)
    expect(contactsChanged([...draftsFrom([LOGIN, PHONE]), blankDraft()], [LOGIN, PHONE])).toBe(false)
    expect(contactsChanged([], [LOGIN, PHONE])).toBe(true)
  })

  it('takes a number with a note after it, and refuses one over forty characters', () => {
    expect(problemOf({ ...blankDraft('phone'), value: '345-1234567 int. 2' })).toBeNull()
    expect(problemOf({ ...blankDraft('phone'), value: `3451234567${' '.repeat(31)}x` })).not.toBeNull()
  })
})
