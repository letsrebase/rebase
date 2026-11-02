import { describe, expect, it } from 'vitest'
import { isVista, vistaOf } from './vista'

describe('vistaOf', () => {
  it('opens on «Oggi» when the address says nothing', () => {
    expect(vistaOf({})).toBe('oggi')
  })

  it('opens on the table for the two addresses that point into the whole list', () => {
    expect(vistaOf({ apri: '3f0c2a10-0000-4000-8000-000000000001' })).toBe('tutte')
    expect(vistaOf({ assegnata: 'tutte' })).toBe('tutte')
  })

  it('obeys `?vista=` over what the other keys imply', () => {
    expect(vistaOf({ vista: 'oggi', apri: '3f0c2a10-0000-4000-8000-000000000001' })).toBe('oggi')
    expect(vistaOf({ vista: 'tutte' })).toBe('tutte')
  })
})

describe('isVista', () => {
  it('accepts the two views and nothing else', () => {
    expect(isVista('oggi')).toBe(true)
    expect(isVista('tutte')).toBe(true)
    expect(isVista('domani')).toBe(false)
    expect(isVista(undefined)).toBe(false)
  })
})
