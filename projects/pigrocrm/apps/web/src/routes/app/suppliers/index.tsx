import { createFileRoute } from '@tanstack/react-router'
import { OrganizationsPage } from '@/features/organizations/OrganizationsPage'
import { parseRegistrySearch } from '@/features/organizations/search'

/**
 * «Fornitori» (REB-1053): the registry's suppliers, the same list as «Clienti» on the other role.
 * The `purchases` module owns the path (`ROUTE_MODULES`), so the layout turns it away when that
 * module is off. See `customers/index.tsx` for why the `?search=` term re-keys the page.
 */
function SuppliersRoute() {
  const { search } = Route.useSearch()
  return <OrganizationsPage key={search ?? ''} initialSearch={search ?? ''} role="supplier" />
}

export const Route = createFileRoute('/app/suppliers/')({
  component: SuppliersRoute,
  validateSearch: parseRegistrySearch,
})
