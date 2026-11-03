import { createFileRoute } from '@tanstack/react-router'
import { SlackPanel } from '@/features/slack/SlackPanel'
import { ConsentOutcome } from '@/components/ConsentOutcome'
import { consentOutcome } from '@/features/consent/outcome'

/**
 * Where `GET /api/slack/oauth/callback` lands the browser after an install (REB-739): the
 * Slack twin of `settings/drive.tsx`, with the same reasoning. `validateSearch` narrows
 * `?esito=` to a string or nothing, and `consentOutcome` looks it up in a fixed table
 * instead of rendering it, so a link somebody else crafted cannot put a sentence of their
 * choosing on this page. Admins only (`SETTINGS_TAB_MIN_ROLE`), so there is no outcome
 * to read out in front of the settings gate.
 *
 * Exports nothing but `Route`, on purpose: a route file that exports anything else opts that
 * route out of the router plugin's automatic code splitting.
 */
function SlackSettingsRoute() {
  const { esito } = Route.useSearch()
  return (
    <div className="space-y-4">
      <ConsentOutcome {...consentOutcome('slack', esito)} />
      <SlackPanel />
    </div>
  )
}

export const Route = createFileRoute('/app/settings/slack')({
  validateSearch: (search: Record<string, unknown>): { esito?: string } => ({
    esito: typeof search.esito === 'string' ? search.esito : undefined,
  }),
  component: SlackSettingsRoute,
})
