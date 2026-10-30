import { describe, expect, it } from 'vitest'
import { REGENERATION_REASON_LABELS, regenerationReason } from './notes'

describe('regenerationReason (REB-861)', () => {
  it('has a word for a card whose person or CV was gone when its answer came', () => {
    expect(regenerationReason('unchanged')).toBe('persona o CV non più presenti')
  })

  it('says so when no reason was recorded, and shows a reason it does not know as it is', () => {
    expect(regenerationReason(undefined)).toBe('motivo non registrato')
    expect(regenerationReason('altro')).toBe('altro')
  })

  it('has a label for each of the backend reasons it holds', () => {
    // The backend's own list is held to this file by `test_card_regeneration_trail.py`.
    expect(Object.keys(REGENERATION_REASON_LABELS)).toContain('superseded')
  })
})
