import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { TaskPage } from '@/features/tasks/TaskPage'
import { EVERYONE, ME } from '@/features/tasks/filters'
import { isVista, vistaOf, type Vista } from '@/features/tasks/vista'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function TaskRoute() {
  const search = Route.useSearch()
  const { open, assigned } = search
  const view = vistaOf(search)
  const navigate = useNavigate()
  // `key`: arriving with another id while the page is open is a remount, so the dialog
  // opens for it instead of staying dismissed from the previous one.
  return (
    <TaskPage
      key={`${open ?? ''}|${assigned ?? ''}`}
      view={view}
      onVistaChange={(next) =>
        void navigate({ to: '/app/activities', search: { assigned, view: next }, replace: true })
      }
      openId={open}
      initialAssignee={assigned}
      onDismissOpen={() =>
        // The view stays the one the address implied: taking `?open=` off must not move
        // the page from the table to «Oggi».
        void navigate({ to: '/app/activities', search: { assigned, view }, replace: true })
      }
    />
  )
}

// Only `Route` is exported: a route file exporting anything else opts that route out of
// the router plugin's automatic code-splitting, which is why the page lives in `features/`.
export const Route = createFileRoute('/app/activities')({
  component: TaskRoute,
  // `?open=<id>` is how the global search opens one attività in its dialog. Anything that
  // is not a UUID is dropped, so a hand-edited URL is the plain page and not a request the
  // API refuses.
  //
  // `?assigned=` is whose attività the page opens on: «io», «tutte» or a user id. The Oggi
  // view's «le altre sono nella pagina Attività» carries the person it was showing, so the
  // rows it left out are the ones listed.
  //
  // `?view=` is the tab: «oggi» or «tutte» (`vistaOf` says what an address without it means).
  validateSearch: (
    search: Record<string, unknown>,
  ): { open?: string; assigned?: string; view?: Vista; da?: string } => ({
    open: typeof search.open === 'string' && UUID_RE.test(search.open) ? search.open : undefined,
    assigned:
      typeof search.assigned === 'string' &&
      (search.assigned === ME || search.assigned === EVERYONE || UUID_RE.test(search.assigned))
        ? search.assigned
        : undefined,
    view: isVista(search.view) ? search.view : undefined,
    // `?da=digest` rides along from `/app/tasks?view=today`, where the digest mail's link lands.
    da: typeof search.da === 'string' ? search.da : undefined,
  }),
})
