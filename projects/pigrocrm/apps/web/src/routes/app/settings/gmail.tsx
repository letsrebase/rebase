import { createFileRoute } from '@tanstack/react-router'
import { GmailPanel } from '@/features/gmail/GmailPanel'
import { messaggioEsito } from '@/features/gmail/esito'

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
  const messaggio = messaggioEsito(esito)
  return (
    <div className="space-y-4">
      {messaggio ? (
        <p role="status" className="border bg-muted/50 px-3 py-2 text-sm">
          {messaggio}
        </p>
      ) : null}
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
