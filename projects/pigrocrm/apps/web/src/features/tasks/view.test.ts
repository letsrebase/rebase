import { describe, expect, it } from 'vitest'
import { isView, viewOf } from './view'

describe('vistaOf', () => {
  it('opens on «Oggi» when the address says nothing', () => {
    expect(viewOf({})).toBe('today')
  })

  it('opens on the table for the two addresses that point into the whole list', () => {
    expect(viewOf({ open: '3f0c2a10-0000-4000-8000-000000000001' })).toBe('all')
    expect(viewOf({ assigned: 'all' })).toBe('all')
  })

  it('obeys `?vista=` over what the other keys imply', () => {
    expect(viewOf({ view: 'today', open: '3f0c2a10-0000-4000-8000-000000000001' })).toBe('today')
    expect(viewOf({ view: 'all' })).toBe('all')
  })
})

describe('isVista', () => {
  it('accepts the two views and nothing else', () => {
    expect(isView('today')).toBe(true)
    expect(isView('all')).toBe(true)
    expect(isView('tomorrow')).toBe(false)
    expect(isView(undefined)).toBe(false)
  })
})
