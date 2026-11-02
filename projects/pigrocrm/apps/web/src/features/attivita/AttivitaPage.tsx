import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { AttivitaDialog } from './AttivitaDialog'
import { ME } from './filters'
import { OggiView } from './OggiView'
import { useAttivita } from './queries'
import { TutteView } from './TutteView'
import { VISTE, type Vista } from './vista'

/**
 * `/app/activities`, the one page for attività (REB-873): two views under one title, «Oggi»
 * (what is late and what falls due, the page `/app/oggi` used to be) and «Tutte» (the whole
 * list as a table). A tab strip in the header switches between them.
 *
 * The view comes in as a prop and every change goes back out through `onVistaChange`, like
 * the dashboard's tabs: the route pushes it into the URL, so a link to either view is an
 * address and the digest's old `/app/oggi` can redirect to the first.
 *
 * The attività a link asks to open (`?apri=<id>`, from the global search, a reminder or a
 * mail) opens in its dialog here, over whichever view is current, and is read by id and not
 * looked up in a list: the filters a view opens with (open, mine) may well not include the
 * very row somebody searched for.
 */
export function AttivitaPage({
  vista,
  onVistaChange,
  openId,
  onDismissOpen,
  initialAssignee = ME,
}: {
  vista: Vista
  onVistaChange: (next: Vista) => void
  openId?: string
  /** Whose attività the views open on: `ME`, `EVERYONE` or a user id. */
  initialAssignee?: string
  /** Called when the dialog opened by `openId` is closed: the route takes `?apri=` off the
   *  URL, so the same hit picked again is a new navigation and a refresh does not reopen. */
  onDismissOpen?: () => void
}) {
  const fromSearch = useAttivita(openId)

  const tabs = (
    <TabsList variant="line">
      {VISTE.map((candidate) => (
        <TabsTrigger key={candidate.id} value={candidate.id}>
          {candidate.label}
        </TabsTrigger>
      ))}
    </TabsList>
  )
  const notice = fromSearch.isError ? (
    <div className="pb-4">
      <QueryErrorBanner error={fromSearch.error} />
    </div>
  ) : null

  return (
    <Tabs value={vista} onValueChange={(next) => onVistaChange(next as Vista)}>
      {/* One view is mounted at a time, as on the dashboard: the other would issue its own
          requests to draw nothing. */}
      {vista === 'oggi' ? (
        <OggiView tabs={tabs} notice={notice} initialAssignee={initialAssignee} />
      ) : (
        <TutteView tabs={tabs} notice={notice} initialAssignee={initialAssignee} />
      )}
      {openId !== undefined && fromSearch.data && (
        <AttivitaDialog
          open
          onOpenChange={(open) => !open && onDismissOpen?.()}
          attivita={fromSearch.data}
        />
      )}
    </Tabs>
  )
}
