import { describe, expect, it } from 'vitest'
import { isFiltered, parseEngagementsSearch } from './search'

const ID = '5b2c70c2-0000-4000-8000-000000000001'

describe('parseEngagementsSearch', () => {
  it('lands on «Da fare» with no filter for an empty or mistyped URL', () => {
    expect(parseEngagementsSearch({})).toEqual({
      tab: 'todo',
      freelancer: undefined,
      client: undefined,
      state: undefined,
    })
    expect(parseEngagementsSearch({ tab: 'tutti', state: 'bozza', freelancer: 'x', client: 7 })).toEqual({
      tab: 'todo',
      freelancer: undefined,
      client: undefined,
      state: undefined,
    })
  })

  it('keeps a tab, a state and the two organizations that are ours', () => {
    const search = parseEngagementsSearch({ tab: 'closed', state: 'cancelled', freelancer: ID, client: ID })
    expect(search).toEqual({ tab: 'closed', freelancer: ID, client: ID, state: 'cancelled' })
    expect(isFiltered(search)).toBe(true)
  })

  it('does not count the tab as a filter', () => {
    expect(isFiltered(parseEngagementsSearch({ tab: 'ongoing' }))).toBe(false)
  })
})
