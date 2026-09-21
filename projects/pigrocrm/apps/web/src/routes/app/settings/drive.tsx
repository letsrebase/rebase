import { createFileRoute } from '@tanstack/react-router'
import { DrivePanel } from '@/features/drive/DrivePanel'
import { messaggioEsito } from '@/features/drive/esito'

/**
 * Where `GET /api/drive/oauth/callback` lands the browser after a consent flow --
 * the Drive twin of `routes/app/settings/gmail.tsx`, same reasoning throughout:
 * `validateSearch` narrows `?esito=` to a string or nothing, and `messaggioEsito` looks
 * it up in a fixed table rather than rendering it, so a link somebody else crafted
 * cannot put a sentence of their choosing on this page.
 *
 * Exports nothing but `Route`, on purpose: a route file that exports anything else opts
 * that route out of the router plugin's automatic code splitting, with a
 * `routeTree.gen.ts` warning as the only sign.
 */
function DriveSettingsRoute() {
  const { esito } = Route.useSearch()
  const messaggio = messaggioEsito(esito)
  return (
    <div className="space-y-4">
      {messaggio ? (
        <p role="status" className="rounded-lg border bg-muted/50 px-3 py-2 text-sm">
          {messaggio}
        </p>
      ) : null}
      <DrivePanel />
    </div>
  )
}

export const Route = createFileRoute('/app/settings/drive')({
  validateSearch: (search: Record<string, unknown>): { esito?: string } => ({
    esito: typeof search.esito === 'string' ? search.esito : undefined,
  }),
  component: DriveSettingsRoute,
})
