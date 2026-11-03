import { useState } from 'react'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { TaskDialog } from './TaskDialog'
import { ME } from './filters'
import { TodayView } from './TodayView'
import { useTask } from './queries'
import { AllView } from './AllView'
import { VIEWS, type View } from './view'

/**
 * `/app/activities`, the one page for tasks (REB-873): two views under one title, «Oggi»
 * (what is late and what falls due, the page `/app/oggi` used to be) and «Tutte» (the whole
 * list as a table). A tab strip in the header switches between them.
 *
 * The view comes in as a prop and every change goes back out through `onVistaChange`, like
 * the dashboard's tabs: the route pushes it into the URL, so a link to either view is an
 * address and the digest's old `/app/oggi` can redirect to the first.
 *
 * The tasks a link asks to open (`?open=<id>`, from the global search, a reminder or a
 * mail) opens in its dialog here, over whichever view is current, and is read by id and not
 * looked up in a list: the filters a view opens with (open, mine) may well not include the
 * very row somebody searched for.
 */
export function TaskPage({
  view,
  onViewChange,
  openId,
  onDismissOpen,
  initialAssignee = ME,
}: {
  view: View
  onViewChange: (next: View) => void
  openId?: string
  /** Whose tasks the views open on: `ME`, `EVERYONE` or a user id. */
  initialAssignee?: string
  /** Called when the dialog opened by `openId` is closed: the route takes `?open=` off the
   *  URL, so the same hit picked again is a new navigation and a refresh does not reopen. */
  onDismissOpen?: () => void
}) {
  const fromSearch = useTask(openId)
  // Whose tasks both views show. Held here and not in each view, which is mounted one at
  // a time: «Di chiunque» chosen on «Oggi» is still chosen on «Tutte».
  const [assignee, setAssignee] = useState(initialAssignee)

  const tabs = (
    <TabsList variant="line">
      {VIEWS.map((candidate) => (
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
    <Tabs value={view} onValueChange={(next) => onViewChange(next as View)}>
      {/* One view is mounted at a time, as on the dashboard: the other would issue its own
          requests to draw nothing. */}
      {view === 'today' ? (
        <TodayView
          tabs={tabs}
          notice={notice}
          assignee={assignee}
          onAssigneeChange={setAssignee}
        />
      ) : (
        <AllView
          tabs={tabs}
          notice={notice}
          assignee={assignee}
          onAssigneeChange={setAssignee}
        />
      )}
      {openId !== undefined && fromSearch.data && (
        <TaskDialog
          open
          onOpenChange={(open) => !open && onDismissOpen?.()}
          task={fromSearch.data}
        />
      )}
    </Tabs>
  )
}
