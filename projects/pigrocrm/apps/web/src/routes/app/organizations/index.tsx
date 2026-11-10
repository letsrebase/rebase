import { createFileRoute } from '@tanstack/react-router'
import { OrganizationsPage } from '@/features/organizations/OrganizationsPage'
import { DEFAULT_REGISTRY_ROLE, parseOrganizationsSearch } from '@/features/organizations/search'

/**
 * The `?search=` term is an entry point, not a live mirror of the box. The palette's
 * «vedi tutti» links here carrying the term the user searched for, and `key` makes
 * arriving with a *different* term a remount, so the filter is seeded even when this
 * route is already open. Typing afterwards stays local: pushing every keystroke through
 * the router would make a controlled input wait on a navigation to echo the character
 * back, which is how a fast typist loses characters.
 */
function OrganizationsRoute() {
  const { search, role } = Route.useSearch()
  return (
    <OrganizationsPage
      key={search ?? ''}
      initialSearch={search ?? ''}
      role={role ?? DEFAULT_REGISTRY_ROLE}
    />
  )
}

export const Route = createFileRoute('/app/organizations/')({
  component: OrganizationsRoute,
  // Declared so the palette can link here with a term and the sidebar with a view
  // (`navigate({ to, search })` is typed against this).
  validateSearch: parseOrganizationsSearch,
})
