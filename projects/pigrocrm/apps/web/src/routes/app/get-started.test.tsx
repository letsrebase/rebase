/**
 * The route's half of the Gmail consent flow's way back (REB-222): «Primi passi» keeps a
 * string `esito` for the Gmail door, and nothing else under that name.
 */
import { describe, expect, it } from 'vitest'
import { Route } from './get-started'

describe('the /app/get-started route', () => {
  it('keeps a string esito and drops anything else', () => {
    const validate = Route.options.validateSearch as (search: Record<string, unknown>) => { esito?: string }
    expect(validate({ esito: 'negato' })).toEqual({ esito: 'negato' })
    expect(validate({ esito: ['negato'] })).toEqual({})
    expect(validate({})).toEqual({})
  })
})
