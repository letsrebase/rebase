/**
 * The addresses that open the Tasks page keep answering after «Oggi» became its first
 * view (REB-873): the old `/app/tasks?view=today`, the `?open=` of a search hit, a reminder or a mail,
 * and the `?assigned=` the Oggi view sends the rows it left out with.
 */
import { isRedirect } from '@tanstack/react-router'
import { describe, expect, it } from 'vitest'
import { Route as ActivitiesRoute } from './activities'
import { Route as TodayRoute } from './oggi'

const ID = '3f0c2a10-0000-4000-8000-000000000001'
const validate = ActivitiesRoute.options.validateSearch as (search: Record<string, unknown>) => {
  open?: string
  assigned?: string
  view?: string
  from?: string
}

describe('the /app/activities route', () => {
  it('keeps `?apri=` and `?assegnata=` as before', () => {
    expect(validate({ open: ID, assigned: 'all' })).toEqual({
      open: ID,
      assigned: 'all',
      view: undefined,
      from: undefined,
    })
    expect(validate({ assigned: ID }).assigned).toBe(ID)
    expect(validate({ assigned: 'io' }).assigned).toBe('io')
  })

  it('drops an `apri` that is not an id and an assignee that is none of the three', () => {
    expect(validate({ open: 'drop table', assigned: 'Mario' })).toEqual({
      open: undefined,
      assigned: undefined,
      view: undefined,
      from: undefined,
    })
  })

  it('reads `?vista=` as one of the two views and drops the rest', () => {
    expect(validate({ view: 'today' }).view).toBe('today')
    expect(validate({ view: 'all' }).view).toBe('all')
    expect(validate({ view: 'tomorrow' }).view).toBeUndefined()
  })
})

describe('the /app/oggi route', () => {
  const beforeLoad = TodayRoute.options.beforeLoad as (context: { search: { from?: string } }) => void

  function redirected(search: { from?: string }) {
    try {
      beforeLoad({ search })
    } catch (thrown) {
      return isRedirect(thrown) ? thrown.options : thrown
    }
    return null
  }

  it('redirects to the Attività page on its Oggi view, so old links keep working', () => {
    expect(redirected({})).toMatchObject({ to: '/app/activities', search: { view: 'today' } })
  })

  it('takes the digest mail\'s `?from=digest` along, which is how PostHog sees the return', () => {
    expect(redirected({ from: 'digest' })).toMatchObject({
      to: '/app/activities',
      search: { view: 'today', from: 'digest' },
    })
  })

  it('reads `from` as a string and nothing else', () => {
    const validate = TodayRoute.options.validateSearch as (search: Record<string, unknown>) => { from?: string }
    expect(validate({ from: 'digest' })).toEqual({ from: 'digest' })
    expect(validate({ from: ['digest'] })).toEqual({ from: undefined })
  })

  it('still reads `?da=digest` of the mails sent before the English rename', () => {
    const validate = TodayRoute.options.validateSearch as (search: Record<string, unknown>) => { from?: string }
    expect(validate({ da: 'digest' })).toEqual({ from: 'digest' })
  })
})
