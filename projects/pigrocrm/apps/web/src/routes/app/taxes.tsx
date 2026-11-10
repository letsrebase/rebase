import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { TaxesPage } from '@/features/taxes/TaxesPage'
import { validateTaxesSearch } from '@/features/taxes/search'

/**
 * `/app/taxes?tab=&year=`, «Tasse» (REB-1026). The tab and the tax year are in the URL; the page
 * is `features/taxes/TaxesPage.tsx` and the validator `features/taxes/search.ts`, so this file
 * exports only `Route` and the router plugin keeps code-splitting it. It belongs to the `taxes`
 * module (`ROUTE_MODULES`): with it off the layout's guard answers instead of this page.
 */
function TaxesRoute() {
  const { tab, year } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <TaxesPage
      tab={tab}
      year={year}
      onTabChange={(next) =>
        void navigate({ search: (previous) => ({ ...previous, tab: next }) })
      }
      onYearChange={(next) =>
        void navigate({ search: (previous) => ({ ...previous, year: next }) })
      }
    />
  )
}

export const Route = createFileRoute('/app/taxes')({
  validateSearch: validateTaxesSearch,
  component: TaxesRoute,
})
