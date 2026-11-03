import { describe, expect, it } from 'vitest'
import { isVista, vistaOf } from './vista'

describe('vistaOf', () => {
  it('opens on «Oggi» when the address says nothing', () => {
    expect(vistaOf({})).toBe('today')
  })

  it('opens on the table for the two addresses that point into the whole list', () => {
    expect(vistaOf({ open: '3f0c2a10-0000-4000-8000-000000000001' })).toBe('all')
    expect(vistaOf({ assigned: 'all' })).toBe('all')
  })

  it('obeys `?vista=` over what the other keys imply', () => {
    expect(vistaOf({ view: 'today', open: '3f0c2a10-0000-4000-8000-000000000001' })).toBe('today')
    expect(vistaOf({ view: 'all' })).toBe('all')
  })
})

describe('isVista', () => {
  it('accepts the two views and nothing else', () => {
    expect(isVista('today')).toBe(true)
    expect(isVista('all')).toBe(true)
    expect(isVista('tomorrow')).toBe(false)
    expect(isVista(undefined)).toBe(false)
  })
})
