import { createFileRoute } from '@tanstack/react-router'
import { ApprovalForm } from '@/features/days/ApprovalForm'
import { validateNewApprovalSearch } from '@/features/days/approvals'

/** `/app/approvals/new?contract=&days=`: record a client's approval with its proof (REB-747). */
function NewApprovalRoute() {
  const { contract, days } = Route.useSearch()
  return (
    <ApprovalForm
      key={`${contract ?? ''}|${days ?? ''}`}
      initialContractId={contract}
      initialDayIds={days === undefined ? [] : days.split(',')}
    />
  )
}

export const Route = createFileRoute('/app/approvals/new')({
  validateSearch: validateNewApprovalSearch,
  component: NewApprovalRoute,
})
