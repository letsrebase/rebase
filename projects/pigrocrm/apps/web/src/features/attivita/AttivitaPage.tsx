import { ListChecks, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { DataTable, type DataTableFeatures } from '@/components/DataTable'
import { FilterChips, FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { RowActions } from '@/components/RowActions'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@rebase/ui/popover'
import { toast } from '@rebase/ui/sonner'
import type { ColumnDef } from '@tanstack/react-table'
import {
  useArchiveAttivita,
  useCancelAttivita,
  useCompleteAttivita,
  useReopenAttivita,
  useRestoreAttivita,
  type Attivita,
} from '@/features/calendar/queries'
import { toProblem } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { AssigneeSelect } from './AssigneeSelect'
import { AttivitaDialog } from './AttivitaDialog'
import { RecordLink } from './AttivitaMeta'
import { ME, toFilters, type Scadenza, type Stato } from './filters'
import { LINK_KIND_LABELS, linkOf, type LinkValue } from './links'
import { useAssignees, useAttivita, useAttivitaPages, useRecordLabel } from './queries'
import { RecordPicker } from './RecordPicker'

const STATO_LABELS: Record<Stato, string> = {
  aperta: 'Aperta',
  completata: 'Completata',
  annullata: 'Annullata',
}

const STATO_TONE: Record<Stato, StatusTone> = {
  aperta: 'gold',
  completata: 'ink',
  annullata: 'muted',
}

const SCADENZA_OPTIONS = [
  { value: 'scadute', label: 'Scadute' },
  { value: 'settimana', label: 'Entro 7 giorni' },
  { value: 'senza', label: 'Senza scadenza' },
] as const satisfies readonly { value: Scadenza; label: string }[]

const STATO_OPTIONS = [
  { value: 'aperta', label: 'Aperte' },
  { value: 'completata', label: 'Completate' },
  { value: 'annullata', label: 'Annullate' },
] as const satisfies readonly { value: Stato; label: string }[]

function AssigneeCell({ id }: { id: string | null }) {
  const assignees = useAssignees()
  const name = assignees.nameOf(id)
  if (name === null) return <span className="text-muted-foreground">—</span>
  return <span>{id === assignees.me ? 'Io' : name}</span>
}

function LinkCell({ attivita }: { attivita: Attivita }) {
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
 * `/app/activities`: every attività of the space as a table, with the filters the API
 * has. It opens on what a person opens it for, the open ones assigned to them, and every
 * filter is one press from «all»: the default is a starting point and never a hidden
 * clause (the chips and the select say what is applied).
 *
 * The row opens the same dialog as everywhere else; «Fatto» and «Annulla» are on the row
 * so working through a list is not a dialog per item; the archived ones are one chip
 * away, with «Ripristina» as the way back.
 */
export function AttivitaPage({
  openId,
  onDismissOpen,
  initialAssignee = ME,
}: {
  openId?: string
  /** Whose attività the page opens on: `ME`, `EVERYONE` or a user id. */
  initialAssignee?: string
  /** Called when the dialog opened by `openId` is closed: the route takes `?apri=` off the
   *  URL, so the same hit picked again is a new navigation and a refresh does not reopen. */
  onDismissOpen?: () => void
}) {
  const canWrite = useCanWrite()
  const assignees = useAssignees()
  const [stato, setStato] = useState<Stato | null>('aperta')
  const [scadenza, setScadenza] = useState<Scadenza | null>(null)
  const [assignee, setAssignee] = useState(initialAssignee)
  const [link, setLink] = useState<LinkValue | null>(null)
  const [archiviate, setArchiviate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Attivita | null>(null)
  // Arrived from the global search with `?apri=<id>`: that row opens in its dialog. Read by
  // id and not looked up in the table, because the filters the page opens with (open, mine)
  // may well not include the very row somebody searched for.
  const fromSearch = useAttivita(openId)

  const filters = useMemo(
    () => toFilters({ stato, scadenza, assignee, meId: assignees.me, link, archiviate }),
    [stato, scadenza, assignee, assignees.me, link, archiviate],
  )
  const list = useAttivitaPages(filters)

  const complete = useCompleteAttivita()
  const cancel = useCancelAttivita()
  const reopen = useReopenAttivita()
  const archive = useArchiveAttivita()
  const restore = useRestoreAttivita()

  function act(mutation: { mutate: (id: string, options: object) => void }, id: string, done: string) {
    mutation.mutate(id, {
      onSuccess: () => toast.success(done),
      onError: (error: unknown) => toast.error(toProblem(error).detail),
    })
  }

  // Rebuilt on every render, not memoised: the same call every list page here makes, and
  // a handful of plain objects is not worth a dependency array that repeats the closure.
  const columns: ColumnDef<DataTableFeatures, Attivita>[] = [
      {
        id: 'titolo',
        header: 'Attività',
        cell: ({ row }) => (
          <span className={row.original.stato === 'aperta' ? undefined : 'line-through opacity-60'}>
            {row.original.titolo}
          </span>
        ),
      },
      {
        id: 'scadenza',
        header: 'Scadenza',
        cell: ({ row }) =>
          row.original.scadenza ? (
            formatIsoDateItalian(row.original.scadenza)
          ) : (
            <span className="text-muted-foreground">Senza scadenza</span>
          ),
      },
      {
        id: 'stato',
        header: 'Stato',
        cell: ({ row }) => (
          <StatusPill tone={STATO_TONE[row.original.stato]}>
            {STATO_LABELS[row.original.stato]}
          </StatusPill>
        ),
      },
      {
        id: 'assegnata',
        header: 'Assegnata a',
        cell: ({ row }) => <AssigneeCell id={row.original.assegnata_a} />,
      },
      {
        id: 'collegamento',
        header: 'Collegata a',
        cell: ({ row }) => <LinkCell attivita={row.original} />,
      },
      {
        id: 'azioni',
        header: '',
        meta: { align: 'right', width: '1%' },
        cell: ({ row }) => {
          if (!canWrite) return null
          const item = row.original
          return (
            <span className="flex items-center justify-end gap-2">
              {!archiviate && item.stato === 'aperta' && (
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
                label={`Azioni su ${item.titolo}`}
                items={[
                  { label: 'Modifica', onSelect: () => setEditing(item) },
                  ...(archiviate
                    ? [
                        {
                          label: 'Ripristina',
                          onSelect: () => act(restore, item.id, 'Attività ripristinata'),
                        },
                      ]
                    : [
                        ...(item.stato === 'aperta'
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
        actions={
          canWrite && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="mr-2 size-4" />
              Nuova attività
            </Button>
          )
        }
      >
        <FilterRow>
          <FilterChips
            label="Stato"
            allLabel="Tutti gli stati"
            options={STATO_OPTIONS}
            value={stato}
            onChange={(next) => {
              setStato(next)
              // «Scadute» is open ones only: another state ends it, so the chips never
              // say one thing while the list shows another.
              if (next !== 'aperta' && scadenza === 'scadute') setScadenza(null)
            }}
          />
          <FilterChips
            label="Scadenza"
            allLabel="Qualsiasi scadenza"
            options={SCADENZA_OPTIONS}
            value={scadenza}
            onChange={(next) => {
              setScadenza(next)
              if (next === 'scadute') setStato('aperta')
            }}
          />
          <AssigneeSelect value={assignee} onChange={setAssignee} />
          <LinkFilter value={link} onChange={setLink} />
          <Button
            variant="outline"
            size="sm"
            aria-pressed={archiviate}
            onClick={() => setArchiviate(!archiviate)}
            className="aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background"
          >
            Archiviate
          </Button>
        </FilterRow>
      </PageHeader>

      <div className="px-8 pb-8">
        {fromSearch.isError && (
          <div className="pb-4">
            <QueryErrorBanner error={fromSearch.error} />
          </div>
        )}
        <DataTable
          columns={columns}
          data={list.items}
          isLoading={list.isLoading}
          isError={list.isError}
          error={list.error}
          onRowClick={setEditing}
          emptyMessage={
            archiviate
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

      <AttivitaDialog open={creating} onOpenChange={setCreating} />
      {openId !== undefined && fromSearch.data && (
        <AttivitaDialog
          open
          onOpenChange={(open) => !open && onDismissOpen?.()}
          attivita={fromSearch.data}
        />
      )}
      {editing && (
        <AttivitaDialog
          open
          onOpenChange={(open) => !open && setEditing(null)}
          attivita={editing}
          archived={archiviate}
        />
      )}
    </>
  )
}
