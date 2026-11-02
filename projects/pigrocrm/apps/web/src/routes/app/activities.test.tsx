/**
 * The addresses that open the Attività page keep answering after «Oggi» became its first
 * view (REB-873): the old `/app/oggi`, the `?apri=` of a search hit, a reminder or a mail,
 * and the `?assegnata=` the Oggi view sends the rows it left out with.
 */
import { isRedirect } from '@tanstack/react-router'
import { describe, expect, it } from 'vitest'
import { Route as ActivitiesRoute } from './activities'
import { Route as OggiRoute } from './oggi'

const ID = '3f0c2a10-0000-4000-8000-000000000001'
const validate = ActivitiesRoute.options.validateSearch as (search: Record<string, unknown>) => {
  apri?: string
  assegnata?: string
  vista?: string
}

describe('the /app/activities route', () => {
  it('keeps `?apri=` and `?assegnata=` as before', () => {
    expect(validate({ apri: ID, assegnata: 'tutte' })).toEqual({
      apri: ID,
      assegnata: 'tutte',
      vista: undefined,
    })
    expect(validate({ assegnata: ID }).assegnata).toBe(ID)
    expect(validate({ assegnata: 'io' }).assegnata).toBe('io')
  })

  it('drops an `apri` that is not an id and an assignee that is none of the three', () => {
    expect(validate({ apri: 'drop table', assegnata: 'Mario' })).toEqual({
      apri: undefined,
      assegnata: undefined,
      vista: undefined,
    })
  })

  it('reads `?vista=` as one of the two views and drops the rest', () => {
    expect(validate({ vista: 'oggi' }).vista).toBe('oggi')
    expect(validate({ vista: 'tutte' }).vista).toBe('tutte')
    expect(validate({ vista: 'domani' }).vista).toBeUndefined()
  })
})

describe('the /app/oggi route', () => {
  it('redirects to the Attività page on its Oggi view, so the digest and old links keep working', () => {
    const beforeLoad = OggiRoute.options.beforeLoad as () => void
    let thrown: unknown
    try {
      beforeLoad()
    } catch (error) {
      thrown = error
    }
    expect(isRedirect(thrown)).toBe(true)
    expect((thrown as { options: unknown }).options).toMatchObject({
      to: '/app/activities',
      search: { vista: 'oggi' },
    })
  })
})
