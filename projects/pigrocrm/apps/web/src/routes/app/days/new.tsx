import { createFileRoute } from '@tanstack/react-router'
import { DayForm } from '@/features/days/DayForm'
import { validateNewDaySearch } from '@/features/days/search'

/** `/app/days/new?contract=&data=`: record a worked day (REB-745). */
function NewDayRoute() {
  const { contract, data } = Route.useSearch()
  // Keyed on what seeds the form, so arriving from another contract's page opens a fresh
  // form and not the last one's state.
  return <DayForm key={`${contract ?? ''}|${data ?? ''}`} initialContractId={contract} initialDate={data} />
}

export const Route = createFileRoute('/app/days/new')({
  validateSearch: validateNewDaySearch,
  component: NewDayRoute,
})
