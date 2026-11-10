import { ChevronDown, ListChecks, Plus } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { LoadingState } from '@/components/LoadingState'
import { useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { AssigneeSelect } from './AssigneeSelect'
import { TaskDialog } from './TaskDialog'
import { TaskItem } from './TaskItem'
import { EVERYONE, ME } from './filters'
import { useAssignees, useTaskToday, type TaskToday } from './queries'

type Group = TaskToday['overdue']

/**
 * One of the four sections of the panel (spec 2026-09-03 §5): a heading with the real
 * count, the rows the server sent, and a line pointing at the Tasks page when the
 * section holds more than it shows. Every row is `TaskItem`, the row the record tabs
 * and the Tasks page already use, so «Fatto», «Annulla», «Modifica» and the link to
 * the record behave here exactly as there.
 *
 * `showDueDate` is on wherever the section does not already say the day: «Oggi» is
 * today, and repeating it on every row would be noise.
 */
function Section({
  id,
  title,
  group,
  empty,
  showDueDate,
  tone,
  foldable = false,
  assignee,
}: {
  id: string
  title: string
  group: Group
  empty: string
  showDueDate: boolean
  /** «In ritardo» is the one number of the product that asks for an action (spec §5),
   *  so it is the one heading drawn in the danger tint. */
  tone?: 'danger'
  foldable?: boolean
  /** The select's value, carried to the Tasks page so the rows left out are listed
   *  for the same person and not for the page's own default. */
  assignee: string
}) {
  // An <h2> in both shapes, so heading navigation finds the folding section too.
  const heading = (
    <h2 id={id} className="flex items-baseline gap-2 text-base font-semibold">
      <span className={tone === 'danger' && group.total > 0 ? 'text-destructive' : undefined}>
        {title}
      </span>
      <span className="text-sm font-normal text-muted-foreground">{group.total}</span>
    </h2>
  )
  const body =
    group.items.length === 0 ? (
      <p className="text-sm text-muted-foreground">{empty}</p>
    ) : (
      <>
        <ul className="space-y-3 text-sm">
          {group.items.map((item) => (
            <TaskItem key={item.id} task={item} showDueDate={showDueDate} />
          ))}
        </ul>
        {group.total > group.items.length && (
          <p className="text-sm text-muted-foreground">
            Ne sono mostrate {group.items.length} su {group.total}: le altre sono nella{' '}
            <Link
              to="/app/activities"
              search={{ view: 'all', assigned: assignee }}
              className="underline underline-offset-4"
            >
              scheda Tutte
            </Link>
            .
          </p>
        )}
      </>
    )

  if (foldable) {
    return (
      <details open className="group space-y-3">
        <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
          <ChevronDown
            className="size-4 shrink-0 -rotate-90 transition-transform group-open:rotate-0"
            aria-hidden="true"
          />
          {heading}
        </summary>
        <div className="space-y-3 pt-3">{body}</div>
      </details>
    )
  }
  return (
    <section aria-labelledby={id} className="space-y-3">
      {heading}
      {body}
    </section>
  )
}

/**
 * The «Oggi» view of the Tasks page, and the first one a person sees: what is late,
 * what falls today, what comes in the next seven days and what has no date at all, in that
 * order (spec 2026-09-03 §5; REB-725). It was the page at `/app/oggi`, which now redirects
 * here (REB-873).
 *
 * Opens on the signed-in person's own tasks and says so in the select; «Di chiunque»
 * is one press away. The day is the server's `today` and is printed in the header, so
 * the page reads the same for somebody working in another time zone.
 *
 * The page's tab strip, any notice above the sections and the choice of person come from
 * `TaskPage`, which owns the two views.
 */
export function TodayView({
  tabs,
  notice,
  assignee,
  onAssigneeChange,
}: {
  tabs: ReactNode
  notice?: ReactNode
  /** Whose tasks the view shows: `ME`, `EVERYONE` or a user id. Held by the page, so
   *  the choice survives a switch between its two views. */
  assignee: string
  onAssigneeChange: (next: string) => void
}) {
  const canWrite = useCanWrite()
  const assignees = useAssignees()
  const [creating, setCreating] = useState(false)

  // Waits for the session's user id when the choice is «mine»: asking before it is known
  // would send the whole space's list and replace it a moment later.
  const waiting = assignee === ME && assignees.me === null
  const who = assignee === EVERYONE ? undefined : assignee === ME ? (assignees.me ?? undefined) : assignee
  const panel = useTaskToday(
    { assigned_to: who, include_unassigned: assignee === ME ? true : undefined, limit: 100 },
    { enabled: !waiting },
  )

  return (
    <>
      <PageHeader
        icon={ListChecks}
        title="Attività"
        description={
          panel.data
            ? `Cosa è in ritardo e cosa scade: oggi è il ${formatIsoDateItalian(panel.data.today)}.`
            : 'Cosa è in ritardo e cosa scade.'
        }
        tabs={tabs}
        actions={
          canWrite && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="mr-2 size-4" />
              Nuova attività
            </Button>
          )
        }
      >
        {/* Under the tab strip's rule now, so it gets the air the title row has above it. */}
        <FilterRow className="pt-4">
          <AssigneeSelect value={assignee} onChange={onAssigneeChange} />
        </FilterRow>
      </PageHeader>

      <div className="space-y-8 px-8 pb-8">
        {notice}
        {panel.isError ? (
          <QueryErrorBanner error={panel.error} />
        ) : !panel.data ? (
          <LoadingState />
        ) : (
          <>
            <Section
              id="today-in-late"
              title="In ritardo"
              group={panel.data.overdue}
              empty="Niente in ritardo."
              assignee={assignee}
              showDueDate
              tone="danger"
            />
            <Section
              id="today-today"
              title="Oggi"
              group={panel.data.today_due}
              empty="Niente in scadenza oggi."
              assignee={assignee}
              showDueDate={false}
            />
            <Section
              id="today-upcoming"
              title="Prossimi sette giorni"
              group={panel.data.next_seven_days}
              empty="Niente nei prossimi sette giorni."
              assignee={assignee}
              showDueDate
            />
            <Section
              id="today-no-due-date"
              title="Senza scadenza"
              group={panel.data.without_due_date}
              empty="Niente senza scadenza."
              assignee={assignee}
              showDueDate={false}
              foldable
            />
          </>
        )}
      </div>

      <TaskDialog open={creating} onOpenChange={setCreating} />
    </>
  )
}
