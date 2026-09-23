import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { HomePage } from '@/features/get-started/HomePage'
import { validateHomeSearch, type DashboardSearch } from '@/features/dashboard/search'

/**
 * The Home: the start page while the space is empty, the dashboard with its period in the
 * URL afterwards (REB-222). `HomePage` decides which.
 *
 * The first route in this codebase with `validateSearch` -- list filters everywhere else
 * are component-local `useState`. The period is in the URL because a screenshot or a shared
 * link of a dashboard with no explicit period is a number with no unit (§4). The Home's
 * validator is the dashboard's plus `esito`, the outcome the Gmail consent flow brings
 * back to an empty space's start page.
 *
 * Only `Route` is exported: anything else opts this route out of the router plugin's
 * automatic code-splitting. The page itself is `features/get-started/HomePage.tsx` and
 * the validator `features/dashboard/search.ts`, both so they can be tested directly.
 */
function HomeRoute() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  // No `PageHeader` here: both pages draw their own. The route's whole job is the URL
  // round trip.
  return (
    <HomePage
      search={search}
      // Merged into the existing search rather than replacing it, so changing the tab
      // keeps the period and changing the period keeps the tab.
      onSearchChange={(next: Partial<DashboardSearch>) =>
        void navigate({ search: (previous) => ({ ...previous, ...next }) })
      }
    />
  )
}

export const Route = createFileRoute('/app/')({
  validateSearch: validateHomeSearch,
  component: HomeRoute,
})
