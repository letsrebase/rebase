import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ProposalsPage } from '@/features/proposals/ProposalsPage'
import { validateProposalsSearch } from '@/features/proposals/search'

/**
 * `/app/proposals?stato=`, the Proposte queue (REB-750). The tab is in the URL so a link
 * to the rejected ones reopens them; the page itself is `features/proposals/ProposalsPage.tsx`
 * and the validator `features/proposals/search.ts`, so this file exports only `Route` and
 * the router plugin keeps code-splitting it.
 */
function ProposalsRoute() {
  const { stato } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <ProposalsPage
      stato={stato}
      onStatoChange={(next) => void navigate({ search: { stato: next } })}
    />
  )
}

export const Route = createFileRoute('/app/proposals/')({
  validateSearch: validateProposalsSearch,
  component: ProposalsRoute,
})
