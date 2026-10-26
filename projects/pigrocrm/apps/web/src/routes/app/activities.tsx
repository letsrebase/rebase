import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { AttivitaPage } from '@/features/attivita/AttivitaPage'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function AttivitaRoute() {
  const { apri } = Route.useSearch()
  const navigate = useNavigate()
  // `key`: arriving with another id while the page is open is a remount, so the dialog
  // opens for it instead of staying dismissed from the previous one.
  return (
    <AttivitaPage
      key={apri ?? ''}
      openId={apri}
      onDismissOpen={() => void navigate({ to: '/app/activities', search: {}, replace: true })}
    />
  )
}

// Only `Route` is exported: a route file exporting anything else opts that route out of
// the router plugin's automatic code-splitting, which is why the page lives in `features/`.
export const Route = createFileRoute('/app/activities')({
  component: AttivitaRoute,
  // `?apri=<id>` is how the global search opens one attività in its dialog. Anything that
  // is not a UUID is dropped, so a hand-edited URL is the plain page and not a request the
  // API refuses.
  validateSearch: (search: Record<string, unknown>): { apri?: string } => ({
    apri: typeof search.apri === 'string' && UUID_RE.test(search.apri) ? search.apri : undefined,
  }),
})
