import { useRouterState } from '@tanstack/react-router'
import { useModules } from '@/lib/modules'
import { useOrganization } from '@/features/organizations/queries'
import { REGISTRY_PATH } from './nav'

/**
 * The registry view the reader is on, for `isNavItemActive`: `supplier` lights «Fornitori»,
 * anything else «Clienti». On the list it is the URL's `?role=`; on an organization's own page
 * the URL carries none, so it is the organization's own roles (REB-1038): one that is a supplier
 * and not a customer lights «Fornitori». It reads the row through the page's own query, so it
 * costs no second request, and until the row arrives it stays on «Clienti», as it always did.
 */
export function useRegistryView(): string | undefined {
  const { location } = useRouterState()
  const searchRole = (location.search as { role?: string } | undefined)?.role
  const prefix = `${REGISTRY_PATH}/`
  const id = location.pathname.startsWith(prefix) ? location.pathname.slice(prefix.length).split('/')[0]! : ''
  const organization = useOrganization(id)
  // «Fornitori» is only drawn with purchases on; without it the page stays under «Clienti».
  const purchasesOn = useModules().on('purchases')
  if (id === '') return searchRole
  const roles = organization.data?.roles ?? []
  return purchasesOn && roles.includes('supplier') && !roles.includes('customer') ? 'supplier' : searchRole
}
