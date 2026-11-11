import { useRouterState } from '@tanstack/react-router'
import { useModules } from '@/lib/modules'
import { useOrganization } from '@/features/organizations/queries'
import { registryPageOf, type RegistryPage } from '@/features/organizations/search'
import { ORGANIZATION_PATH } from './nav'

/**
 * The registry list an organization's own page is read as, for `isNavItemActive`: `supplier`
 * lights «Fornitori», anything else «Clienti» (REB-1038). The two lists are pages of their own
 * (REB-1053) and light themselves by their path, so this answers only on
 * `/app/organizations/<id>`, from the organization's roles: one that is a supplier and not a
 * customer is «Fornitori». It reads the row through the page's own query, so it costs no second
 * request, and until the row arrives it stays on «Clienti». Anywhere else it is `undefined`.
 */
export function useRegistryView(): RegistryPage | undefined {
  const { location } = useRouterState()
  const prefix = `${ORGANIZATION_PATH}/`
  const id = location.pathname.startsWith(prefix) ? location.pathname.slice(prefix.length).split('/')[0]! : ''
  const organization = useOrganization(id)
  // «Fornitori» is only drawn with purchases on; without it the page stays under «Clienti».
  const purchasesOn = useModules().on('purchases')
  if (id === '') return undefined
  return registryPageOf(organization.data?.roles ?? [], purchasesOn)
}
