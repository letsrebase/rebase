import { createFileRoute } from '@tanstack/react-router'
import { PeoplePage } from '@/features/people/PeoplePage'
import { parsePeopleSearch } from '@/features/people/search'

/**
 * The `?search=` term is an entry point, not a live mirror of the box. The palette's
 * «vedi tutti» links here carrying the term the user searched for, and `key` makes
 * arriving with a *different* term a remount, so the filter is seeded even when this
 * route is already open. Typing afterwards stays local: pushing every keystroke through
 * the router would make a controlled input wait on a navigation to echo the character
 * back, which is how a fast typist loses characters.
 *
 * `customer_id` is not part of that `key`: unlike `search`, the «Azienda» select is a
 * controlled prop of `PeoplePage` (see its own docstring), so a change to it re-renders
 * the page instead of remounting it.
 */
function PeopleRoute() {
  const { search, customer_id } = Route.useSearch()
  return <PeoplePage key={search ?? ''} initialSearch={search ?? ''} customerId={customer_id} />
}

export const Route = createFileRoute('/app/people/')({
  component: PeopleRoute,
  // Declared so the palette can link here with a term (`navigate({ to, search })` is
  // typed against this).
  validateSearch: parsePeopleSearch,
})
