import { HandCoins, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { DataTable } from '@/components/DataTable'
import { FilterChips, FilterRow } from '@/components/FilterRow'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useCostCategories } from '@/features/purchases/queries'
import { formatMoneyValue } from '@/features/time/columns'
import { useIsAdmin } from '@/lib/auth'
import { MONEY_SCALE, sumDecimalStrings } from '@/lib/decimal'
import { useLegalEntity } from '@/lib/legalEntity'
import { NewNote } from './NewNote'
import { noteColumns } from './columns'
import { NoteCard } from './NoteCells'
import { RejectDialog, ReimburseDialog } from './NoteDialogs'
import { NoteActions, NoteBody } from './NotePanel'
import {
  EMPTY_APPROVER,
  EMPTY_STAFF,
  NOT_LINKED,
  NO_MATCH,
  OWN_NOTE,
  PAGE_TITLE,
  STATUS_LABELS,
  TABS,
  actionsFor,
  notes as notesWord,
  refusedAsOwn,
} from './labels'
import {
  useExpenseNote,
  useExpenseNotes,
  useExpenseSummary,
  useStaffChoices,
  type Bucket,
  type ExpenseNote,
  type ExpenseNoteSummary,
  type NoteStatus,
  type NotesParams,
} from './queries'
import { STATUSES, type ExpenseNotesSearch } from './search'

/** The table from `md` up, the inline panel from `lg`: below them rows are cards and the panel is
 *  a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

/** `Select` needs a non-empty string for «no filter»; the API parameter is simply left out. */
const ANY = 'all'

export interface ExpenseNotesPageProps {
  search: ExpenseNotesSearch
  /** Changes the URL's reading; opening a note is a step of its own, a filter replaces the last one. */
  onSearchChange: (patch: Partial<ExpenseNotesSearch>, options?: { replace?: boolean }) => void
}

/**
 * «Note spese» (REB-1046, the approved brief of 2026-11-10): the expense notes of the staff
 * module, from submission to reimbursement. A staff member reads their own and sends a new one; an
 * administrator decides (tabs «Da approvare», «Da rimborsare» and «Chiuse») and, on «Da rimborsare»,
 * chooses the way of paying a person back. Who the caller is among the staff comes from the
 * server's summary, since `GET /api/staff` is an administrator's. Nothing here computes a figure but
 * the total of one person's notes on «Da rimborsare», which the page reads in full for that.
 */
export function ExpenseNotesPage(props: ExpenseNotesPageProps) {
  const admin = useIsAdmin()
  const summary = useExpenseSummary()
  const header = <PageHeader icon={HandCoins} title={PAGE_TITLE} />
  if (summary.isError) {
    return (
      <>
        {header}
        <div className="px-8 pb-8">
          <QueryErrorBanner error={summary.error} onRetry={() => void summary.refetch()} />
        </div>
      </>
    )
  }
  if (summary.data === undefined) {
    return (
      <>
        {header}
        <LoadingState label="Leggo le note spese…" className="px-8 py-8" />
      </>
    )
  }
  if (!admin && summary.data.staff_member_id === null) {
    // A collaborator or a reader the company has not linked to a person: no figure, no request.
    return (
      <>
        {header}
        <p role="alert" className="mx-8 border bg-card p-4 text-sm text-muted-foreground">
          {NOT_LINKED}
        </p>
      </>
    )
  }
  return <Notes {...props} admin={admin} summary={summary.data} />
}

/** One person's notes, in the order the list gave them. */
function groupByPerson(items: ExpenseNote[]): { id: string; name: string; notes: ExpenseNote[] }[] {
  const groups = new Map<string, { id: string; name: string; notes: ExpenseNote[] }>()
  for (const note of items) {
    const group = groups.get(note.staff_member_id) ?? { id: note.staff_member_id, name: note.staff_member_name, notes: [] }
    group.notes.push(note)
    groups.set(note.staff_member_id, group)
  }
  return [...groups.values()]
}

function Figure({ label, value, caption }: { label: string; value: string; caption: string }) {
  return (
    <div>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
      <dd className="text-sm text-muted-foreground">{caption}</dd>
    </div>
  )
}

/** The count beside a tab's name, when there is one. */
function Count({ value }: { value: number }) {
  return value > 0 ? <span className="ml-2 tabular-nums text-muted-foreground">{value}</span> : null
}

function Notes({
  admin,
  summary,
  search,
  onSearchChange,
}: ExpenseNotesPageProps & { admin: boolean; summary: ExpenseNoteSummary }) {
  const wide = useMediaQuery(TABLE)
  const inline = useMediaQuery(INLINE_PANEL)
  const { legal_entities, several } = useLegalEntity()
  const sole = summary.only_administrator
  const tab: Bucket = search.tab ?? 'to_approve'
  const params: NotesParams = admin
    ? {
        bucket: tab,
        staff_member_id: search.person,
        category_id: search.category,
        legal_entity_id: several ? search.legal_entity : undefined,
      }
    : { status: search.status }
  const list = useExpenseNotes(params, { scoped: admin })
  const categories = useCostCategories(true)
  const people = useStaffChoices(admin)
  const [creating, setCreating] = useState(false)
  const [rejecting, setRejecting] = useState<ExpenseNote | null>(null)
  const [reimbursing, setReimbursing] = useState<{ name: string; notes: ExpenseNote[] } | null>(null)

  // «Da rimborsare» groups a person's notes and totals them, so it reads every page: a group
  // missing a note would total, and reimburse, less than the person is owed.
  const reading = admin && tab === 'to_reimburse'
  const { hasMore, isFetchingMore, isError, loadMore } = list
  useEffect(() => {
    if (reading && hasMore && !isFetchingMore && !isError) loadMore()
  }, [reading, hasMore, isFetchingMore, isError, loadMore])

  const openId = search.note
  const fromList = list.items.find((candidate) => candidate.id === openId)
  // The list is asked first: a note it holds is read from it, and only one it does not hold is asked for.
  const fetched = useExpenseNote(openId, fromList === undefined && !list.isLoading)
  const open = fromList ?? fetched.data ?? null

  const closeNote = () => onSearchChange({ note: undefined })
  const columns = noteColumns({ withPerson: admin, forStaffMember: !admin })
  const filtered = admin
    ? search.person !== undefined || search.category !== undefined || (several && search.legal_entity !== undefined)
    : search.status !== undefined

  const newNoteButton = summary.can_submit ? (
    <Button onClick={() => setCreating(true)}>
      <Plus className="mr-2 size-4" aria-hidden />
      Nuova nota spese
    </Button>
  ) : null

  const total = summary.awaiting_reimbursement
  const figures = (
    <dl className="flex flex-wrap items-baseline gap-x-8 gap-y-4 px-8 pb-4 pt-4">
      <Figure
        label="In attesa di rimborso"
        value={formatMoneyValue(total)}
        caption="Note inviate o approvate, non ancora rimborsate"
      />
      {admin && list.totalCount !== undefined && list.totalAmount !== undefined && !list.isError ? (
        <Figure
          label={TABS.find((candidate) => candidate.id === tab)?.label ?? ''}
          value={formatMoneyValue(list.totalAmount)}
          caption={notesWord(list.totalCount)}
        />
      ) : null}
    </dl>
  )

  const counts: Record<Bucket, number> = {
    to_approve: summary.to_approve,
    to_reimburse: summary.to_reimburse,
    closed: summary.closed,
  }

  const filters = admin ? (
    <FilterRow className="grid grid-cols-2 sm:flex sm:flex-wrap">
      <Select
        value={search.person ?? ANY}
        onValueChange={(next) => onSearchChange({ person: next === ANY ? undefined : next, note: undefined }, { replace: true })}
      >
        <SelectTrigger aria-label="Persona" className="w-full sm:w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>Persona</SelectItem>
          {(people.data?.items ?? []).map((member) => (
            <SelectItem key={member.id} value={member.id}>
              {member.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={search.category ?? ANY}
        onValueChange={(next) => onSearchChange({ category: next === ANY ? undefined : next, note: undefined }, { replace: true })}
      >
        <SelectTrigger aria-label="Categoria" className="w-full sm:w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>Categoria</SelectItem>
          {(categories.data ?? []).map((category) => (
            <SelectItem key={category.id} value={category.id}>
              {category.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {several ? (
        <Select
          value={search.legal_entity ?? ANY}
          onValueChange={(next) =>
            onSearchChange({ legal_entity: next === ANY ? undefined : next, note: undefined }, { replace: true })
          }
        >
          <SelectTrigger aria-label="Azienda" className="w-full sm:w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>Azienda</SelectItem>
            {legal_entities.map((entity) => (
              <SelectItem key={entity.id} value={entity.id}>
                {entity.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
    </FilterRow>
  ) : (
    <FilterRow>
      <FilterChips<NoteStatus>
        label="Stato"
        allLabel="Tutte"
        options={STATUSES.map((status) => ({ value: status, label: STATUS_LABELS[status] }))}
        value={search.status ?? null}
        onChange={(next) => onSearchChange({ status: next ?? undefined, note: undefined }, { replace: true })}
      />
    </FilterRow>
  )

  const segmented =
    admin && !wide ? (
      <div role="group" aria-label="Elenco" className="mx-8 mb-4 grid grid-cols-3 border">
        {TABS.map((candidate) => (
          <button
            key={candidate.id}
            type="button"
            aria-pressed={tab === candidate.id}
            onClick={() => onSearchChange({ tab: candidate.id, note: undefined }, { replace: true })}
            className="min-h-14 border-r px-2 py-1 text-sm last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring aria-pressed:bg-foreground aria-pressed:text-background"
          >
            <span className="block">{candidate.label}</span>
            <span className="block text-xs tabular-nums opacity-80">{counts[candidate.id]}</span>
          </button>
        ))}
      </div>
    ) : null

  const tabs =
    admin && wide ? (
      <TabsList variant="line">
        {TABS.map((candidate) => (
          <TabsTrigger key={candidate.id} value={candidate.id}>
            {candidate.label}
            <Count value={counts[candidate.id]} />
          </TabsTrigger>
        ))}
      </TabsList>
    ) : undefined

  const openNote = (id: string) => onSearchChange({ note: id })
  const rowsOf = (rows: ExpenseNote[], label: string) =>
    wide ? (
      <DataTable label={label} columns={columns} data={rows} onRowClick={(row) => openNote(row.id)} />
    ) : (
      <ul aria-label={label} className="space-y-3">
        {rows.map((note) => (
          <NoteCard
            key={note.id}
            note={note}
            withPerson={admin}
            forStaffMember={!admin}
            selected={note.id === openId}
            onOpen={() => openNote(note.id)}
          />
        ))}
      </ul>
    )

  let body
  if (list.isError && list.items.length === 0) {
    // No list and no figure under the banner: nothing is offered that never arrived.
    body = <QueryErrorBanner error={list.error} onRetry={list.retry} />
  } else if (list.isLoading || (reading && list.hasMore && !list.isError)) {
    body = <LoadingState label="Leggo le note spese…" className="py-8" />
  } else if (list.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-4 border bg-card p-6">
        <p className="font-medium">
          {filtered ? NO_MATCH : admin ? EMPTY_APPROVER[tab] : EMPTY_STAFF}
        </p>
        {!admin && !filtered ? newNoteButton : null}
      </div>
    )
  } else if (reading) {
    body = (
      <div className="space-y-8">
        {list.isError ? <QueryErrorBanner error={list.error} onRetry={list.retry} /> : null}
        {groupByPerson(list.items).map((group) => {
          const decidable = group.notes.filter((note) => actionsFor(note, admin, sole).includes('reimburse'))
          return (
            <section key={group.id} aria-label={`Note di ${group.name}`} className="space-y-3">
              <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
                <div className="min-w-0">
                  <h2 className="break-words font-semibold">{group.name}</h2>
                  <p className="text-sm tabular-nums text-muted-foreground">
                    {notesWord(group.notes.length)},{' '}
                    {formatMoneyValue(sumDecimalStrings(group.notes.map((note) => note.amount), MONEY_SCALE))}
                  </p>
                </div>
                {decidable.length > 0 ? (
                  <Button variant="outline" onClick={() => setReimbursing({ name: group.name, notes: decidable })}>
                    Rimborsa…
                  </Button>
                ) : refusedAsOwn(group.notes[0]!, admin, sole) ? (
                  <p className="max-w-md text-sm text-muted-foreground">{OWN_NOTE}</p>
                ) : null}
              </header>
              {rowsOf(group.notes, `Note di ${group.name}`)}
            </section>
          )
        })}
      </div>
    )
  } else {
    body = rowsOf(list.items, PAGE_TITLE)
  }

  const panelBody =
    open === null ? (
      fetched.isError ? (
        <div className="p-4">
          <QueryErrorBanner error={fetched.error} />
        </div>
      ) : (
        <LoadingState label="Leggo la nota…" className="p-4" />
      )
    ) : (
      <NoteBody note={open} admin={admin} sole={sole} forStaffMember={!admin} />
    )
  const panelActions =
    open !== null && actionsFor(open, admin, sole).length > 0 ? (
      <NoteActions
        note={open}
        admin={admin}
        sole={sole}
        onReject={() => setRejecting(open)}
        onReimburse={() => setReimbursing({ name: open.staff_member_name, notes: [open] })}
        onDecided={closeNote}
      />
    ) : null

  const page = (
    <>
      <PageHeader
        icon={HandCoins}
        title={PAGE_TITLE}
        description={
          admin
            ? 'Le spese anticipate da chi lavora in azienda: le approvi, poi scegli come rimborsarle.'
            : 'Le spese che hai anticipato, dall’invio al rimborso.'
        }
        actions={newNoteButton}
        tabs={tabs}
      >
        {figures}
        {segmented}
        {filters}
      </PageHeader>

      <div className={cn('flex gap-6 px-8 pb-8 pt-2', inline ? 'items-start' : '')}>
        <div className="min-w-0 flex-1">
          {body}
          {list.hasMore && !reading && list.totalCount !== undefined ? (
            <LoadMore
              label={`Mostra altri (${list.totalCount} in tutto)`}
              message={`Mostrate ${list.items.length} note su ${list.totalCount}.`}
              isFetchingMore={list.isFetchingMore}
              hasError={list.isError}
              error={list.error}
              onLoadMore={list.loadMore}
            />
          ) : null}
        </div>
        {openId !== undefined && inline ? (
          <aside
            aria-label="Nota aperta"
            className="sticky top-4 flex max-h-[calc(100dvh-2rem)] w-82.5 shrink-0 flex-col self-start overflow-hidden border bg-card"
          >
            <div className="flex justify-end border-b px-2 py-1">
              <Button variant="ghost" size="sm" onClick={closeNote}>
                Chiudi
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">{panelBody}</div>
            {panelActions === null ? null : <div className="border-t p-3">{panelActions}</div>}
          </aside>
        ) : null}
      </div>

      {openId !== undefined && !inline ? (
        <Sheet open onOpenChange={(next) => !next && closeNote()}>
          <SheetContent side="right" closeLabel="Chiudi" className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none">
            <SheetTitle className="sr-only">Nota aperta</SheetTitle>
            <SheetDescription className="sr-only">La ricevuta, i dettagli e cosa si può fare.</SheetDescription>
            <div className="min-h-0 flex-1 overflow-y-auto pt-12">{panelBody}</div>
            {panelActions === null ? null : (
              <div className="sticky bottom-0 border-t bg-background p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                {panelActions}
              </div>
            )}
          </SheetContent>
        </Sheet>
      ) : null}

      <NewNote open={creating} onOpenChange={setCreating} />
      {rejecting === null ? null : (
        <RejectDialog
          note={rejecting}
          onClose={() => setRejecting(null)}
          onRejected={() => {
            setRejecting(null)
            closeNote()
          }}
        />
      )}
      {reimbursing === null ? null : (
        <ReimburseDialog
          personName={reimbursing.name}
          notes={reimbursing.notes}
          onClose={() => {
            setReimbursing(null)
            closeNote()
          }}
        />
      )}
    </>
  )

  return admin && wide ? (
    <Tabs
      value={tab}
      onValueChange={(next) => onSearchChange({ tab: next as Bucket, note: undefined }, { replace: true })}
      className="gap-0"
    >
      {page}
    </Tabs>
  ) : (
    page
  )
}
