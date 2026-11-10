import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { IvaPage } from '@/features/iva/IvaPage'
import { validateIvaSearch } from '@/features/iva/search'

/**
 * `/app/vat?year=&period=&frequency=&register=&entity=`, «IVA» (REB-1024). The year, the period, the register
 * and the legal entity are in the URL; the page is `features/iva/IvaPage.tsx` and the validator
 * `features/iva/search.ts`, so this file exports only `Route` and the router plugin keeps
 * code-splitting it. It belongs to the `purchases` module (`ROUTE_MODULES`): with it off the
 * layout's guard answers instead of this page.
 */
function VatRoute() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <IvaPage
      search={search}
      onSearch={(patch) =>
        void navigate({ search: (previous) => ({ ...previous, ...patch }), replace: true })
      }
    />
  )
}

export const Route = createFileRoute('/app/vat')({
  validateSearch: validateIvaSearch,
  component: VatRoute,
})
