import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { DaysPage } from '@/features/days/DaysPage'
import { validateDaysSearch } from '@/features/days/search'

/**
 * `/app/days?contract=&mese=`, the register of contract days (REB-745). Only `Route` is
 * exported, so the router plugin keeps code-splitting it; the page is
 * `features/days/DaysPage.tsx` and the validator `features/days/search.ts`.
 */
function DaysRoute() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return <DaysPage search={search} onSearchChange={(next) => void navigate({ search: next })} />
}

export const Route = createFileRoute('/app/days/')({
  validateSearch: validateDaysSearch,
  component: DaysRoute,
})
