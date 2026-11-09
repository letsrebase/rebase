import { describe, expect, it } from 'vitest'
import { Route as ItalianDetail } from '../clienti/$customerId'
import { Route as ItalianIndex } from '../clienti/index'
import { Route as OldDetail } from './$customerId'
import { Route as OldIndex } from './index'

type Thrown = { options: { to: string; params?: unknown; search?: unknown } }

function redirectOf(run: () => void): Thrown['options'] {
  try {
    run()
  } catch (thrown) {
    return (thrown as Thrown).options
  }
  throw new Error('the route did not redirect')
}

// REB-969: the registry is «organizations»; a bookmark, a sent mail and a reminder still
// carry the old paths, and each lands on the page they meant.
describe('the retired customer paths', () => {
  it('sends /app/customers/<id> to the organization with the same id', () => {
    const beforeLoad = OldDetail.options.beforeLoad as (ctx: unknown) => void
    expect(redirectOf(() => beforeLoad({ params: { customerId: 'c1' } }))).toMatchObject({
      to: '/app/organizations/$organizationId',
      params: { organizationId: 'c1' },
    })
  })

  it('sends /app/customers to the list, keeping the search term the palette carried', () => {
    const beforeLoad = OldIndex.options.beforeLoad as (ctx: unknown) => void
    expect(redirectOf(() => beforeLoad({ search: { search: 'acme' } }))).toMatchObject({
      to: '/app/organizations',
      search: { search: 'acme' },
    })
  })

  it('declares the search term it carries, so the router does not drop it', () => {
    const validate = OldIndex.options.validateSearch as (s: Record<string, unknown>) => unknown
    expect(validate({ search: 'acme' })).toEqual({ search: 'acme' })
    expect(validate({ search: '' })).toEqual({ search: undefined })
  })

  it('still sends the Italian /app/clienti paths to the same places', () => {
    const detail = ItalianDetail.options.beforeLoad as (ctx: unknown) => void
    const index = ItalianIndex.options.beforeLoad as (ctx: unknown) => void
    expect(redirectOf(() => detail({ params: { customerId: 'c1' } }))).toMatchObject({
      to: '/app/organizations/$organizationId',
      params: { organizationId: 'c1' },
    })
    expect(redirectOf(() => index({}))).toMatchObject({ to: '/app/organizations' })
  })
})
