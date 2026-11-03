/**
 * The route's half of the Gmail consent flow's way back (REB-222): «Primi passi» keeps a
 * string `outcome` for the Gmail door, and nothing else under that name.
 */
import { describe, expect, it } from 'vitest'
import { Route } from './get-started'

describe('the /app/get-started route', () => {
  it('keeps a string esito and drops anything else', () => {
    const validate = Route.options.validateSearch as (search: Record<string, unknown>) => { outcome?: string }
    expect(validate({ outcome: 'denied' })).toEqual({ outcome: 'denied' })
    expect(validate({ outcome: ['denied'] })).toEqual({})
    expect(validate({})).toEqual({})
  })
})
