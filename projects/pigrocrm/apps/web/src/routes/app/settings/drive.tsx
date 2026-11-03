import { createFileRoute } from '@tanstack/react-router'
import { DrivePanel } from '@/features/drive/DrivePanel'
import { ConsentOutcome } from '@/components/ConsentOutcome'
import { consentOutcome } from '@/features/consent/outcome'

/**
 * Where `GET /api/drive/oauth/callback` lands the browser after a consent flow --
 * the Drive twin of `routes/app/settings/gmail.tsx`, same reasoning throughout:
 * `validateSearch` narrows `?outcome=` to a string or nothing, and `consentOutcome` looks
 * it up in a fixed table rather than rendering it, so a link somebody else crafted
 * cannot put a sentence of their choosing on this page.
 *
 * An admin and a collaboratore both land here (REB-457): the Drive credential is per CRM
 * user, so a collaboratore reads their own consent's outcome above their own panel.
 *
 * Exports nothing but `Route`, on purpose: a route file that exports anything else opts
 * that route out of the router plugin's automatic code splitting, with a
 * `routeTree.gen.ts` warning as the only sign.
 */
function DriveSettingsRoute() {
  const { outcome } = Route.useSearch()
  return (
    <div className="space-y-4">
      <ConsentOutcome {...consentOutcome('drive', outcome)} />
      <DrivePanel />
    </div>
  )
}

export const Route = createFileRoute('/app/settings/drive')({
  validateSearch: (search: Record<string, unknown>): { outcome?: string } => ({
    outcome: typeof search.outcome === 'string' ? search.outcome : undefined,
  }),
  component: DriveSettingsRoute,
})
