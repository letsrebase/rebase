import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { AttivitaPage } from '@/features/attivita/AttivitaPage'
import { EVERYONE, ME } from '@/features/attivita/filters'
import { isVista, vistaOf, type Vista } from '@/features/attivita/vista'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function AttivitaRoute() {
  const search = Route.useSearch()
  const { apri, assegnata } = search
  const vista = vistaOf(search)
  const navigate = useNavigate()
  // `key`: arriving with another id while the page is open is a remount, so the dialog
  // opens for it instead of staying dismissed from the previous one.
  return (
    <AttivitaPage
      key={`${apri ?? ''}|${assegnata ?? ''}`}
      vista={vista}
      onVistaChange={(next) =>
        void navigate({ to: '/app/activities', search: { assegnata, vista: next }, replace: true })
      }
      openId={apri}
      initialAssignee={assegnata}
      onDismissOpen={() =>
        // The view stays the one the address implied: taking `?apri=` off must not move
        // the page from the table to «Oggi».
        void navigate({ to: '/app/activities', search: { assegnata, vista }, replace: true })
      }
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
  //
  // `?assegnata=` is whose attività the page opens on: «io», «tutte» or a user id. The Oggi
  // view's «le altre sono nella pagina Attività» carries the person it was showing, so the
  // rows it left out are the ones listed.
  //
  // `?vista=` is the tab: «oggi» or «tutte» (`vistaOf` says what an address without it means).
  validateSearch: (
    search: Record<string, unknown>,
  ): { apri?: string; assegnata?: string; vista?: Vista } => ({
    apri: typeof search.apri === 'string' && UUID_RE.test(search.apri) ? search.apri : undefined,
    assegnata:
      typeof search.assegnata === 'string' &&
      (search.assegnata === ME || search.assegnata === EVERYONE || UUID_RE.test(search.assegnata))
        ? search.assegnata
        : undefined,
    vista: isVista(search.vista) ? search.vista : undefined,
  }),
})
