import { describe, expect, it } from 'vitest'
import { editsAgainst, hiddenWithData, proposedState, withDependencies, type Proposal } from './model'

function module(key: Proposal['state']['modules'][number]['key'], enabled: boolean, records = 0) {
  return {
    key,
    label: key,
    description: '',
    requires: key === 'time' ? ['deals' as const] : [],
    enabled,
    stored: null,
    default: true,
    holds_data: records > 0,
    records,
  }
}

const proposal: Proposal = {
  profile: 'solo',
  previous_profile: null,
  applied: false,
  changes: [
    { module: 'deals', label: 'Deal', was: true, becomes: false, records: 4 },
    { module: 'time', label: 'Ore', was: true, becomes: false, records: 0 },
  ],
  state: {
    profile: null,
    onboarding: 'pending',
    modules: [
      module('deals', true, 4),
      module('contracts', true),
      module('time', true),
      module('costs', true),
      module('taxes', true),
    ],
  },
}

describe('the onboarding model', () => {
  it('proposes today\'s state with the profile\'s changes laid over it', () => {
    expect(proposedState(proposal)).toEqual({
      deals: false,
      contracts: true,
      time: false,
      costs: true,
      taxes: true,
    })
  })

  it('sends only the edits that differ from the profile\'s defaults', () => {
    const wanted = { ...proposedState(proposal), deals: true }
    expect(editsAgainst(proposal, wanted)).toEqual({ deals: true })
    expect(editsAgainst(proposal, proposedState(proposal))).toEqual({})
  })

  it('names the modules with records that are about to go off, and not the ones kept', () => {
    expect(hiddenWithData(proposal, proposedState(proposal))).toEqual([
      { module: 'deals', label: 'deals', records: 4 },
    ])
    expect(hiddenWithData(proposal, { ...proposedState(proposal), deals: true })).toEqual([])
  })

  it('switches a module off when the one it needs is off', () => {
    expect(withDependencies({ deals: false, time: true }, { time: ['deals'] })).toEqual({
      deals: false,
      time: false,
    })
    expect(withDependencies({ deals: true, time: true }, { time: ['deals'] })).toEqual({
      deals: true,
      time: true,
    })
  })
})
