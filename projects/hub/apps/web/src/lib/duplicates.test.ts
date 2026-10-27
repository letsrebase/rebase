import { describe, expect, it } from 'vitest'
import type { DuplicateList, MergeResult } from './api'
import { mergeResultLine, pairCount } from './duplicates'

const MERGED: MergeResult = {
  survivor_freelancer_id: 'f2',
  absorbed_freelancer_id: 'f1',
  action_id: 'act-1',
  moved: { logins: 3, comments: 2, matches: 1 },
  lasciati: {},
  cv: 'invariato',
  scheda_da_rigenerare: true,
}

describe('the line a merge leaves (REB-800)', () => {
  it('counts the rows moved, whole, and asks for the card to be written again', () => {
    expect(mergeResultLine(MERGED)).toBe('Unite: 6 righe spostate. La scheda anonima va riscritta.')
  })

  it('says «1 riga spostata» for one and leaves the card alone when it needs nothing', () => {
    expect(mergeResultLine({ ...MERGED, moved: { logins: 1 }, scheda_da_rigenerare: false })).toBe(
      'Unite: 1 riga spostata.',
    )
  })

  it('says how many rows stayed where they were', () => {
    expect(mergeResultLine({ ...MERGED, scheda_da_rigenerare: false, lasciati: { referrals_made: 2 } })).toBe(
      'Unite: 6 righe spostate. 2 righe sono rimaste dov’erano perché la scheda tenuta ne aveva già una uguale.',
    )
    expect(mergeResultLine({ ...MERGED, scheda_da_rigenerare: false, lasciati: { fiscal: 1 } })).toBe(
      'Unite: 6 righe spostate. 1 riga è rimasta dov’era perché la scheda tenuta ne aveva già una uguale.',
    )
  })
})

describe('the number of pairs to look at (REB-800)', () => {
  it('counts the two groups together and ignores the sign-ups, which have nothing to merge', () => {
    const pair = { survivor: {}, assorbito: {}, segnali: [], unibile: true }
    const list = { unibili: [pair, pair], da_rivedere: [pair], iscrizioni: 5 } as unknown as DuplicateList
    expect(pairCount(list)).toBe(3)
  })
})
