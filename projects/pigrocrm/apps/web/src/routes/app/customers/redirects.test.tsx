import { describe, expect, it } from 'vitest'
import { Route as ItalianDetail } from '../clienti/$customerId'
import { Route as ItalianIndex } from '../clienti/index'
import { Route as OrganizationsIndex } from '../organizations/index'
import { Route as OldDetail } from './$customerId'

type Thrown = { options: { to: string; params?: unknown; search?: unknown } }

function redirectOf(run: () => void): Thrown['options'] {
  try {
    run()
  } catch (thrown) {
    return (thrown as Thrown).options
  }
  throw new Error('the route did not redirect')
}

/** What `/app/organizations?…` does: the route validates its search, then redirects on it. */
function organizationsRedirect(query: Record<string, unknown>) {
  const validate = OrganizationsIndex.options.validateSearch as (s: Record<string, unknown>) => unknown
  const beforeLoad = OrganizationsIndex.options.beforeLoad as (ctx: unknown) => void
  return redirectOf(() => beforeLoad({ search: validate(query) }))
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

  it('still sends the Italian /app/clienti paths to the same places', () => {
    const detail = ItalianDetail.options.beforeLoad as (ctx: unknown) => void
    const index = ItalianIndex.options.beforeLoad as (ctx: unknown) => void
    expect(redirectOf(() => detail({ params: { customerId: 'c1' } }))).toMatchObject({
      to: '/app/organizations/$organizationId',
      params: { organizationId: 'c1' },
    })
    expect(redirectOf(() => index({}))).toMatchObject({ to: '/app/customers' })
  })
})

// REB-1053: «Clienti» and «Fornitori» are a page each, and the one «Anagrafiche» URL that every
// old bookmark and sent link holds lands on the right one.
describe('the retired /app/organizations list', () => {
  it('sends the bare path to «Clienti», as the page always was', () => {
    expect(organizationsRedirect({})).toMatchObject({ to: '/app/customers', search: {} })
  })

  it('sends ?role=supplier to «Fornitori»', () => {
    expect(organizationsRedirect({ role: 'supplier' })).toMatchObject({ to: '/app/suppliers' })
  })

  it('sends ?role=customer and the retired ?role=any («Tutte») to «Clienti»', () => {
    expect(organizationsRedirect({ role: 'customer' })).toMatchObject({ to: '/app/customers' })
    expect(organizationsRedirect({ role: 'any' })).toMatchObject({ to: '/app/customers' })
    expect(organizationsRedirect({ role: 'nonsense' })).toMatchObject({ to: '/app/customers' })
  })

  it('carries the search term the palette or a bookmark held, to either page', () => {
    expect(organizationsRedirect({ search: 'acme' })).toMatchObject({
      to: '/app/customers',
      search: { search: 'acme' },
    })
    expect(organizationsRedirect({ role: 'supplier', search: 'acme' })).toMatchObject({
      to: '/app/suppliers',
      search: { search: 'acme' },
    })
  })

  it('drops an empty term and never forwards the role it consumed', () => {
    const landed = organizationsRedirect({ role: 'supplier', search: '' })
    expect(landed.search).toEqual({ search: undefined })
    expect(landed.search).not.toHaveProperty('role')
  })
})
