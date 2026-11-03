import { createFileRoute } from '@tanstack/react-router'
import { GmailPanel } from '@/features/gmail/GmailPanel'
import { ConsentOutcome } from '@/components/ConsentOutcome'
import { consentOutcome } from '@/features/consent/outcome'

/**
 * Where `GET /api/gmail/oauth/callback` lands the browser after a consent flow.
 *
 * `validateSearch` narrows `?outcome=` to a string or nothing, and `consentOutcome` looks
 * it up in a fixed table rather than rendering it -- so a link somebody else crafted
 * cannot put a sentence of their choosing on this page. An unknown code shows nothing
 * and the panel below speaks for itself.
 *
 * Exports nothing but `Route`, on purpose: a route file that exports anything else opts
 * that route out of the router plugin's automatic code splitting, with a
 * `routeTree.gen.ts` warning as the only sign.
 */
function GmailSettingsRoute() {
  const { outcome } = Route.useSearch()
  return (
    <div className="space-y-4">
      <ConsentOutcome {...consentOutcome('gmail', outcome)} />
      <GmailPanel />
    </div>
  )
}

export const Route = createFileRoute('/app/settings/gmail')({
  validateSearch: (search: Record<string, unknown>): { outcome?: string } => ({
    outcome: typeof search.outcome === 'string' ? search.outcome : undefined,
  }),
  component: GmailSettingsRoute,
})
