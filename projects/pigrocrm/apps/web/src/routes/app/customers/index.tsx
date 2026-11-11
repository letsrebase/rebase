import { createFileRoute } from '@tanstack/react-router'
import { OrganizationsPage } from '@/features/organizations/OrganizationsPage'
import { parseRegistrySearch } from '@/features/organizations/search'

/**
 * «Clienti» (REB-1053): the registry's customers, its own page since «Fornitori» got one.
 *
 * The `?search=` term is an entry point, not a live mirror of the box. The palette's
 * «vedi tutti» links here carrying the term the user searched for, and `key` makes
 * arriving with a *different* term a remount, so the filter is seeded even when this
 * route is already open. Typing afterwards stays local: pushing every keystroke through
 * the router would make a controlled input wait on a navigation to echo the character
 * back, which is how a fast typist loses characters.
 */
function CustomersRoute() {
  const { search } = Route.useSearch()
  return <OrganizationsPage key={search ?? ''} initialSearch={search ?? ''} role="customer" />
}

export const Route = createFileRoute('/app/customers/')({
  component: CustomersRoute,
  // Declared so the palette can link here with a term (`navigate({ to, search })` is typed
  // against this).
  validateSearch: parseRegistrySearch,
})
