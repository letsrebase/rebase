import { describe, expect, it } from 'vitest'
import { canReceiveApproval, excerptIsInBody, toDateTimeLocal, validateNewApprovalSearch } from './approvals'

const ID = '5b2c70c2-0000-4000-8000-000000000001'

describe('which days can use an approval', () => {
  it('is a proposed day and a day worked without approval, and neither once it has one', () => {
    expect(canReceiveApproval({ stato: 'proposto', approval_id: null })).toBe(true)
    expect(canReceiveApproval({ stato: 'lavorato_senza_approvazione', approval_id: null })).toBe(true)
    expect(canReceiveApproval({ stato: 'approvato', approval_id: null })).toBe(false)
    expect(canReceiveApproval({ stato: 'lavorato', approval_id: null })).toBe(false)
    expect(canReceiveApproval({ stato: 'proposto', approval_id: ID })).toBe(false)
  })
})

describe('excerptIsInBody', () => {
  const body = 'Buongiorno,\nconfermo la giornata\ndel 2 marzo.\nSaluti'
  it('finds a passage wrapped differently and in another case', () => {
    expect(excerptIsInBody('Confermo la giornata del 2 marzo.', body)).toBe(true)
  })
  it('says no when the passage is not there, and nothing is wrong with no excerpt yet', () => {
    expect(excerptIsInBody('confermo il 3 marzo', body)).toBe(false)
    expect(excerptIsInBody('   ', body)).toBe(true)
  })
})

describe('the form’s URL and clock', () => {
  it('keeps a contract and the days that are ids, and drops the rest', () => {
    expect(validateNewApprovalSearch({ contract: ID, days: `${ID},nope,${ID}` })).toEqual({
      contract: ID,
      days: `${ID},${ID}`,
    })
    expect(validateNewApprovalSearch({ contract: 'x', days: 'y' })).toEqual({ contract: undefined, days: undefined })
  })

  it('writes the local time a datetime-local field wants, not the UTC one', () => {
    expect(toDateTimeLocal(new Date(2026, 2, 1, 9, 5))).toBe('2026-03-01T09:05')
  })
})
