import { ListChecks, Plus } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { DataTable, type DataTableFeatures } from '@/components/DataTable'
import { FilterChips, FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { RowActions } from '@/components/RowActions'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@rebase/ui/popover'
import { toast } from '@rebase/ui/sonner'
import type { ColumnDef } from '@tanstack/react-table'
import {
  useArchiveTask,
  useCancelTask,
  useCompleteTask,
  useReopenTask,
  useRestoreTask,
  type Task,
} from '@/features/calendar/queries'
import { toProblem } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { AssigneeSelect } from './AssigneeSelect'
import { TaskDialog } from './TaskDialog'
import { RecordLink } from './TaskMeta'
import { toFilters, type DueDate, type Status } from './filters'
import { LINK_KIND_LABELS, linkOf, type LinkValue } from './links'
import { useAssignees, useTaskPages, useRecordLabel } from './queries'
import { RecordPicker } from './RecordPicker'

const StatusLabels: Record<Status, string> = {
  open: 'Aperta',
  completed: 'Completata',
  cancelled: 'Annullata',
}

const StatusTone: Record<Status, StatusTone> = {
  open: 'gold',
  completed: 'ink',
  cancelled: 'muted',
}

const DueDateOptions = [
  { value: 'overdue', label: 'Scadute' },
  { value: 'week', label: 'Entro 7 giorni' },
  { value: 'without', label: 'Senza scadenza' },
] as const satisfies readonly { value: DueDate; label: string }[]

const StatusOptions = [
  { value: 'open', label: 'Aperte' },
  { value: 'completed', label: 'Completate' },
  { value: 'cancelled', label: 'Annullate' },
] as const satisfies readonly { value: Status; label: string }[]

function AssigneeCell({ id }: { id: string | null }) {
  const assignees = useAssignees()
  const name = assignees.nameOf(id)
  if (name === null) return <span className="text-muted-foreground">—</span>
  return <span>{id === assignees.me ? 'Io' : name}</span>
}

function LinkCell({ attivita }: { attivita: Task }) {
  const link = linkOf(attivita)
  if (link === null) return <span className="text-muted-foreground">—</span>
  return (
    // The link goes to the record; the click must not also open the row's dialog.
    <span onClick={(event) => event.stopPropagation()}>
      <RecordLink link={link} />
    </span>
  )
}

/** The button of the «Collegata a» filter: the record chosen, or what it would choose. */
function LinkFilter({
  value,
  onChange,
}: {
  value: LinkValue | null
  onChange: (next: LinkValue | null) => void
}) {
  const label = useRecordLabel(value)
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          {value ? `${LINK_KIND_LABELS[value.kind]} · ${label ?? '…'}` : 'Collegata a: qualsiasi'}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-96">
        <RecordPicker id="filtro-collegamento" value={value} onChange={onChange} />
      </PopoverContent>
    </Popover>
  )
}

/**
 * The «Tutte» view of the Attività page: every attività of the space as a table, with the
 * filters the API has. It opens on what a person opens it for, the open ones assigned to
 * them, and every filter is one press from «all»: the default is a starting point and never
 * a hidden clause (the chips and the select say what is applied).
 *
 * The row opens the same dialog as everywhere else; «Fatto» and «Annulla» are on the row
 * so working through a list is not a dialog per item; the archived ones are one chip
 * away, with «Ripristina» as the way back.
 *
 * The page's tab strip, any notice above the table, the choice of person and the dialog
 * `?open=` opens come from `TaskPage`, which owns the two views (REB-873).
 */
export function AllView({
  tabs,
  notice,
  assignee,
  onAssigneeChange,
}: {
  tabs: ReactNode
  notice?: ReactNode
  /** Whose attività the view shows: `ME`, `EVERYONE` or a user id. Held by the page, so
   *  the choice survives a switch between its two views. */
  assignee: string
  onAssigneeChange: (next: string) => void
}) {
  const canWrite = useCanWrite()
  const assignees = useAssignees()
  const [status, setStatus] = useState<Status | null>('open')
  const [due_date, setDueDate] = useState<DueDate | null>(null)
  const [link, setLink] = useState<LinkValue | null>(null)
  const [archived, setArchived] = useState(false)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Task | null>(null)
  const filters = useMemo(
    () => toFilters({ status, due_date, assignee, meId: assignees.me, link, archived }),
    [status, due_date, assignee, assignees.me, link, archived],
  )
  const list = useTaskPages(filters)

  const complete = useCompleteTask()
  const cancel = useCancelTask()
  const reopen = useReopenTask()
  const archive = useArchiveTask()
  const restore = useRestoreTask()

  function act(mutation: { mutate: (id: string, options: object) => void }, id: string, done: string) {
    mutation.mutate(id, {
      onSuccess: () => toast.success(done),
      onError: (error: unknown) => toast.error(toProblem(error).detail),
    })
  }

  // Rebuilt on every render, not memoised: the same call every list page here makes, and
  // a handful of plain objects is not worth a dependency array that repeats the closure.
  const columns: ColumnDef<DataTableFeatures, Task>[] = [
      {
        id: 'title',
        header: 'Attività',
        cell: ({ row }) => (
          <span className={row.original.status === 'open' ? undefined : 'line-through opacity-60'}>
            {row.original.title}
          </span>
        ),
      },
      {
        id: 'due_date',
        header: 'Scadenza',
        cell: ({ row }) =>
          row.original.due_date ? (
            formatIsoDateItalian(row.original.due_date)
          ) : (
            <span className="text-muted-foreground">Senza scadenza</span>
          ),
      },
      {
        id: 'status',
        header: 'Stato',
        cell: ({ row }) => (
          <StatusPill tone={StatusTone[row.original.status]}>
            {StatusLabels[row.original.status]}
          </StatusPill>
        ),
      },
      {
        id: 'assigned',
        header: 'Assegnata a',
        cell: ({ row }) => <AssigneeCell id={row.original.assigned_to} />,
      },
      {
        id: 'link',
        header: 'Collegata a',
        cell: ({ row }) => <LinkCell attivita={row.original} />,
      },
      {
        id: 'actions',
        header: '',
        meta: { align: 'right', width: '1%' },
        cell: ({ row }) => {
          if (!canWrite) return null
          const item = row.original
          return (
            <span className="flex items-center justify-end gap-2">
              {!archived && item.status === 'open' && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(event) => {
                    event.stopPropagation()
                    act(complete, item.id, 'Fatto')
                  }}
                >
                  Fatto
                </Button>
              )}
              <RowActions
                label={`Azioni su ${item.title}`}
                items={[
                  { label: 'Modifica', onSelect: () => setEditing(item) },
                  ...(archived
                    ? [
                        {
                          label: 'Ripristina',
                          onSelect: () => act(restore, item.id, 'Attività ripristinata'),
                        },
                      ]
                    : [
                        ...(item.status === 'open'
                          ? [{ label: 'Annulla', onSelect: () => act(cancel, item.id, 'Annullata') }]
                          : [{ label: 'Riapri', onSelect: () => act(reopen, item.id, 'Attività riaperta') }]),
                        {
                          label: 'Archivia',
                          destructive: true,
                          onSelect: () => act(archive, item.id, 'Attività archiviata'),
                        },
                      ]),
                ]}
              />
            </span>
          )
        },
      },
  ]

  return (
    <>
      <PageHeader
        icon={ListChecks}
        title="Attività"
        description="Tutto quello che c’è da fare, con scadenza o senza."
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
          <FilterChips
            label="Stato"
            allLabel="Tutti gli stati"
            options={StatusOptions}
            value={status}
            onChange={(next) => {
              setStatus(next)
              // «Scadute» is open ones only: another state ends it, so the chips never
              // say one thing while the list shows another.
              if (next !== 'open' && due_date === 'overdue') setDueDate(null)
            }}
          />
          <FilterChips
            label="Scadenza"
            allLabel="Qualsiasi scadenza"
            options={DueDateOptions}
            value={due_date}
            onChange={(next) => {
              setDueDate(next)
              if (next === 'overdue') setStatus('open')
            }}
          />
          <AssigneeSelect value={assignee} onChange={onAssigneeChange} />
          <LinkFilter value={link} onChange={setLink} />
          <Button
            variant="outline"
            size="sm"
            aria-pressed={archived}
            onClick={() => setArchived(!archived)}
            className="aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background"
          >
            Archiviate
          </Button>
        </FilterRow>
      </PageHeader>

      <div className="px-8 pb-8">
        {notice}
        <DataTable
          label="Attività"
          columns={columns}
          data={list.items}
          isLoading={list.isLoading}
          isError={list.isError}
          error={list.error}
          onRowClick={setEditing}
          emptyMessage={
            archived
              ? 'Nessuna attività archiviata con questi filtri.'
              : 'Nessuna attività con questi filtri.'
          }
        />
        {list.hasMore && (
          <div className="flex justify-center pt-4">
            <Button variant="outline" disabled={list.isFetchingMore} onClick={list.loadMore}>
              {list.isFetchingMore ? 'Caricamento…' : 'Carica altre'}
            </Button>
          </div>
        )}
      </div>

      <TaskDialog open={creating} onOpenChange={setCreating} />
      {editing && (
        <TaskDialog
          open
          onOpenChange={(open) => !open && setEditing(null)}
          attivita={editing}
          archived={archived}
        />
      )}
    </>
  )
}
