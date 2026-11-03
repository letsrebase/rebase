import { createFileRoute } from '@tanstack/react-router'
import { DealsList } from '@/features/deals/DealsList'
import { booleanSearchParam } from '@/lib/searchParams'

/**
 * The `?search=` term is an entry point, not a live mirror of the box. The palette's
 * «vedi tutti» links here carrying the term the user searched for, and `key` makes
 * arriving with a *different* term a remount, so the filter is seeded even when this
 * route is already open. Typing afterwards stays local: pushing every keystroke through
 * the router would make a controlled input wait on a navigation to echo the character
 * back, which is how a fast typist loses characters.
 */
function DealsListRoute() {
  const { search, invoiced_unwon, to_invoice } = Route.useSearch()
  return (
    <DealsList
      key={search ?? ''}
      initialSearch={search ?? ''}
      filters={{ invoiced_unwon, to_invoice }}
    />
  )
}

export const Route = createFileRoute('/app/deal/list')({
  component: DealsListRoute,
  // Declared so the palette can link here with a term, and so the operational dashboard
  // can link here with a drill-through (`<Link to={...} search={...} />` is typed against
  // this function's *return* type, not its parameter type). An empty or non-string value
  // is dropped rather than carried as `?search=`, so the URL never claims a filter that is
  // not applied -- and the two booleans are carried only when they are literally `true`,
  // so `?to_invoice=false` is the absence of a filter rather than a third state.
  validateSearch: (
    search: Record<string, unknown>,
  ): { search?: string; invoiced_unwon?: boolean; to_invoice?: boolean } => ({
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
    invoiced_unwon: booleanSearchParam(search.invoiced_unwon),
    to_invoice: booleanSearchParam(search.to_invoice),
  }),
})
