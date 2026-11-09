import { createFileRoute } from '@tanstack/react-router'
import { OrganizationsPage } from '@/features/organizations/OrganizationsPage'

/**
 * The `?search=` term is an entry point, not a live mirror of the box. The palette's
 * «vedi tutti» links here carrying the term the user searched for, and `key` makes
 * arriving with a *different* term a remount, so the filter is seeded even when this
 * route is already open. Typing afterwards stays local: pushing every keystroke through
 * the router would make a controlled input wait on a navigation to echo the character
 * back, which is how a fast typist loses characters.
 */
function OrganizationsRoute() {
  const { search } = Route.useSearch()
  return <OrganizationsPage key={search ?? ''} initialSearch={search ?? ''} />
}

export const Route = createFileRoute('/app/organizations/')({
  component: OrganizationsRoute,
  // Declared so the palette can link here with a term (`navigate({ to, search })` is
  // typed against this). An empty or non-string value is dropped rather than carried as
  // `?search=`, so the URL never claims a filter that is not applied.
  validateSearch: (search: Record<string, unknown>): { search?: string } => ({
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
  }),
})
