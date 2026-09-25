import { createFileRoute } from '@tanstack/react-router'
import { GmailPanel } from '@/features/gmail/GmailPanel'
import { ConsentOutcome } from '@/components/ConsentOutcome'
import { messaggioEsito, riprovaEsito } from '@/features/gmail/esito'

/**
 * Where `GET /api/gmail/oauth/callback` lands the browser after a consent flow.
 *
 * `validateSearch` narrows `?esito=` to a string or nothing, and `messaggioEsito` looks
 * it up in a fixed table rather than rendering it -- so a link somebody else crafted
 * cannot put a sentence of their choosing on this page. An unknown code shows nothing
 * and the panel below speaks for itself.
 *
 * Exports nothing but `Route`, on purpose: a route file that exports anything else opts
 * that route out of the router plugin's automatic code splitting, with a
 * `routeTree.gen.ts` warning as the only sign.
 */
function GmailSettingsRoute() {
  const { esito } = Route.useSearch()
  return (
    <div className="space-y-4">
      <ConsentOutcome messaggio={messaggioEsito(esito)} riprova={riprovaEsito(esito)} />
      <GmailPanel />
    </div>
  )
}

export const Route = createFileRoute('/app/settings/gmail')({
  validateSearch: (search: Record<string, unknown>): { esito?: string } => ({
    esito: typeof search.esito === 'string' ? search.esito : undefined,
  }),
  component: GmailSettingsRoute,
})
