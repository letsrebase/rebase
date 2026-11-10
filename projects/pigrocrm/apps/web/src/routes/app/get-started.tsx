import { createFileRoute } from '@tanstack/react-router'
import { GetStartedPage } from '@/features/get-started/GetStartedPage'

/**
 * «Primi passi», the start page. `?outcome=` is the Gmail consent flow's outcome, which
 * the callback brings here for a person who may not open Impostazioni (REB-222); it is
 * narrowed to a string or nothing and looked up in a fixed table, never rendered as
 * given. Exports nothing but `Route`, which keeps the route code-split.
 */
function GetStartedRoute() {
  const { outcome } = Route.useSearch()
  return <GetStartedPage outcome={outcome} />
}

export const Route = createFileRoute('/app/get-started')({
  validateSearch: (search: Record<string, unknown>): { outcome?: string } =>
    typeof search.outcome === 'string' ? { outcome: search.outcome } : {},
  component: GetStartedRoute,
})
