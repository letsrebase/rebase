import { FileInput, Upload, X } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { FilterRow } from '@/components/FilterRow'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { cn } from '@rebase/ui/cn'
import { Loader } from '@rebase/ui/loader'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { toast } from '@rebase/ui/sonner'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { formatAmount } from '@/features/proposals/fields'
import { toProblem } from '@/lib/api'
import { useAuth, useCanWrite, useIsAdmin } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { CompanyDialog, DiscardDialog } from './InboxDialogs'
import { InboxPanel } from './InboxPanel'
import {
  ACCEPT,
  KIND_LABELS,
  PICKABLE_KINDS,
  STATE_LABELS,
  STATE_TONES,
  TABS,
  formatDay,
  documents,
  kindWord,
  visibilityLine,
  whyLine,
  type InboxItem,
  type InboxKind,
  type InboxState,
  type InboxTab,
} from './labels'
import {
  useClassifyPending,
  useDiscardItem,
  useInboxItem,
  useInboxList,
  useModelReads,
  useSetKind,
  useStateTotals,
  useUnreadCount,
} from './queries'
import { useUploader, type UploadRow } from './useUploader'

/** The tables's width from `md`, the inline panel's from `lg`: below them rows are cards and the
 *  panel is a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

const ALL = 'all'
type Origin = 'all' | 'direct' | 'archive'

const ORIGINS: Record<Origin, string> = {
  all: 'Da dove',
  direct: 'Caricati da te o da un collega',
  archive: 'Da un archivio ZIP',
}

const EMPTY_TEXT =
  'Qui arrivano fatture, ricevute, estratti conto e contratti, da un caricamento o da una casella email.'

/** «da lotto.zip»: the archive an item was unpacked from, by name (one read per archive). */
function ParentChip({ parentId }: { parentId: string }) {
  const parent = useInboxItem(parentId)
  return (
    <span className="border px-1.5 text-xs text-muted-foreground">
      da {parent.data?.original_name ?? 'archivio'}
    </span>
  )
}

/** The document cell: its name, then «origine, data», then the chips that say more. */
function Document({ item, me, admin }: { item: InboxItem; me: string | null; admin: boolean }) {
  const mine = item.uploaded_by_id === me
  const origin =
    item.uploaded_by_id === null ? 'Dallo spazio' : mine ? 'Caricato da te' : 'Caricato da un collega'
  const onlyYou = !admin && item.legal_entity_id === null && mine
  return (
    <div className="min-w-0 space-y-1">
      <p className="whitespace-normal break-words font-medium">{item.original_name}</p>
      <p className="text-xs text-muted-foreground">
        {origin}, {formatDay(item.created_at)}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {item.parent_id === null ? null : <ParentChip parentId={item.parent_id} />}
        {onlyYou ? (
          <span
            className="border px-1.5 text-xs text-muted-foreground"
            title={visibilityLine(item, me, admin) ?? undefined}
          >
            Solo tu
          </span>
        ) : null}
      </div>
    </div>
  )
}

/** The amount an item states, in euros as the API sent it («1.464,00 €»), or nothing. */
function amountOf(item: InboxItem): string {
  return item.amount == null ? '' : (formatAmount(item.amount, item.currency) ?? '')
}

/** What a tab holds in all: the totals of the states it lists. */
function tabCount(
  states: readonly InboxState[],
  totals: Partial<Record<InboxState, number>>,
): number {
  return states.reduce((sum, state) => sum + (totals[state] ?? 0), 0)
}

function StateCell({ item }: { item: InboxItem }) {
  return (
    <div className="max-w-80 space-y-1">
      <StatusPill tone={STATE_TONES[item.state]}>{STATE_LABELS[item.state]}</StatusPill>
      <p className="whitespace-normal text-xs text-muted-foreground">{whyLine(item)}</p>
    </div>
  )
}

function Sending({ row, onDismiss }: { row: UploadRow; onDismiss: () => void }) {
  return (
    <div
      role={row.status === 'refused' ? 'alert' : 'status'}
      className={cn(
        'flex items-center gap-3 border px-4 py-3 text-sm',
        row.status === 'refused'
          ? 'border-destructive/50 bg-destructive/10 text-destructive'
          : 'bg-card text-muted-foreground',
      )}
    >
      {row.status === 'sending' ? <Loader className="size-4 shrink-0" /> : null}
      <div className="min-w-0 flex-1">
        <p className="break-words font-medium text-foreground">{row.name}</p>
        <p>{row.status === 'sending' ? 'Lo leggo…' : row.reason}</p>
      </div>
      {row.status === 'refused' ? (
        <Button variant="ghost" size="icon-sm" aria-label={`Chiudi l’avviso su ${row.name}`} onClick={onDismiss}>
          <X className="size-4" aria-hidden />
        </Button>
      ) : null}
    </div>
  )
}

/**
 * «In arrivo» (REB-984, the brief of REB-973): every file that reached the space and is not a
 * record yet. A table with the open item in a panel beside it from `lg`, cards and a full-screen
 * sheet below; three tabs; filters; upload by the button, by dropping a file anywhere on the page
 * or from the phone's «+»; and, for an administrator with the model on, «Leggi i documenti in
 * attesa». What a person sees is the API's to decide (REB-960): a limited view shows fewer rows
 * and a smaller count, and nothing here widens it.
 */
export function InboxPage({
  tab,
  item,
  onTabChange,
  onItemChange,
}: {
  tab: InboxTab
  item: string | null
  onTabChange: (next: InboxTab) => void
  onItemChange: (next: string | null) => void
}) {
  const { user } = useAuth()
  const me = user?.id ?? null
  const admin = useIsAdmin()
  const canWrite = useCanWrite()
  const wide = useMediaQuery(TABLE)
  const inline = useMediaQuery(INLINE_PANEL)
  const { legal_entities, several } = useLegalEntity()
  const [kind, setKind] = useState<InboxKind | null>(null)
  const [origin, setOrigin] = useState<Origin>('all')
  const [company, setCompany] = useState<string | null>(null)
  const [selected, setSelected] = useState<readonly string[]>([])
  const [bulk, setBulk] = useState<'discard' | 'company' | null>(null)
  const [dragging, setDragging] = useState(false)
  const picker = useRef<HTMLInputElement>(null)

  const list = useInboxList(tab, kind)
  const totals = useStateTotals()
  const modelReads = useModelReads()
  const unread = useUnreadCount(modelReads)
  const classify = useClassifyPending()
  const uploader = useUploader()
  const discard = useDiscardItem()
  const assign = useSetKind()

  const items = list.items.filter(
    (candidate) =>
      (origin === 'all' || (origin === 'archive') === (candidate.parent_id !== null)) &&
      (company === null || candidate.legal_entity_id === company),
  )
  const chosen = selected.filter((id) => items.some((candidate) => candidate.id === id))
  const bulkable = canWrite && tab === 'pending'

  function pick(files: FileList | File[] | null) {
    const chosenFiles = Array.from(files ?? [])
    if (chosenFiles.length > 0) void uploader.upload(chosenFiles)
  }

  function dropped(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    if (canWrite) pick(event.dataTransfer.files)
  }

  function toggle(id: string) {
    setSelected((previous) =>
      previous.includes(id) ? previous.filter((candidate) => candidate !== id) : [...previous, id],
    )
  }

  /** One write for every selected item, sent one after the other: a refusal on one (it was
   *  decided meanwhile) does not stop the rest, and the toast says how it went. */
  async function forEach(ids: readonly string[], act: (id: string) => Promise<unknown>, done: string) {
    let failure: unknown = null
    let count = 0
    for (const id of ids) {
      try {
        await act(id)
        count += 1
      } catch (error) {
        failure ??= error
      }
    }
    if (count > 0) {
      setSelected([])
      toast.success(`${documents(count)} ${done}.`)
      if (failure !== null) toast.error(toProblem(failure).detail)
      return
    }
    // Nothing was done: the dialog stays open with the server's own sentence, and the selection.
    if (failure !== null) throw failure
  }

  const header = (
    <PageHeader
      icon={FileInput}
      title="In arrivo"
      description="I file arrivati nello spazio che aspettano una persona: guarda cosa è stato letto, poi decidi cosa farne."
      actions={
        <>
          {modelReads && unread > 0 && canWrite ? (
            <Button
              variant="outline"
              disabled={classify.isPending}
              onClick={() =>
                classify.mutate(undefined, {
                  onSuccess: (page) =>
                    toast.success(
                      page.items.length === 0
                        ? 'Non c’era niente da leggere.'
                        : `Letti ${documents(page.items.length)}.`,
                    ),
                  onError: (error) => toast.error(toProblem(error).detail),
                })
              }
            >
              {classify.isPending ? <Loader className="mr-2 size-4" /> : null}
              Leggi i documenti in attesa
            </Button>
          ) : null}
          {canWrite && wide ? (
            <Button disabled={uploader.busy} onClick={() => picker.current?.click()}>
              <Upload className="mr-2 size-4" aria-hidden />
              Carica documenti
            </Button>
          ) : null}
        </>
      }
      tabs={
        <TabsList variant="line">
          {TABS.map((candidate) => (
            <TabsTrigger key={candidate.id} value={candidate.id}>
              {candidate.label}
              {tabCount(candidate.states, totals) > 0 ? (
                <span className="ml-2 tabular-nums text-muted-foreground">
                  {tabCount(candidate.states, totals)}
                </span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
      }
    >
      <FilterRow className="grid grid-cols-2 sm:flex sm:flex-wrap">
        <Select
          value={kind ?? ALL}
          onValueChange={(next) => setKind(next === ALL ? null : (next as InboxKind))}
        >
          <SelectTrigger aria-label="Tipo" className="w-full sm:w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Tipo</SelectItem>
            {PICKABLE_KINDS.map((candidate) => (
              <SelectItem key={candidate} value={candidate}>
                {KIND_LABELS[candidate]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={origin} onValueChange={(next) => setOrigin(next as Origin)}>
          <SelectTrigger aria-label="Da dove" className="w-full sm:w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(ORIGINS) as Origin[]).map((candidate) => (
              <SelectItem key={candidate} value={candidate}>
                {ORIGINS[candidate]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {several ? (
          <Select
            value={company ?? ALL}
            onValueChange={(next) => setCompany(next === ALL ? null : next)}
          >
            <SelectTrigger aria-label="Azienda" className="w-full sm:w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Azienda</SelectItem>
              {legal_entities.map((entity) => (
                <SelectItem key={entity.id} value={entity.id}>
                  {entity.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </FilterRow>
    </PageHeader>
  )

  const body = list.isError ? (
    <div className="space-y-3">
      <QueryErrorBanner error={list.error} />
      <Button variant="outline" size="sm" onClick={list.refetch}>
        Riprova
      </Button>
    </div>
  ) : list.isLoading ? (
    <LoadingState label="Leggo i nuovi arrivi…" className="py-8" />
  ) : items.length === 0 && uploader.rows.length === 0 ? (
    <div className="border border-dashed px-6 py-12 text-center">
      <p className="mx-auto max-w-md text-sm text-muted-foreground">
        {tab === 'pending' && kind === null && origin === 'all' && company === null
          ? EMPTY_TEXT
          : 'Nessun documento qui.'}
      </p>
      {canWrite && tab === 'pending' ? (
        <Button className="mt-4" onClick={() => picker.current?.click()}>
          <Upload className="mr-2 size-4" aria-hidden />
          Carica documenti
        </Button>
      ) : null}
      {list.hasMore ? (
        <div className="mt-4 flex justify-center">
          <Button variant="outline" size="sm" onClick={list.fetchMore} disabled={list.isFetchingMore}>
            Mostra altri
          </Button>
        </div>
      ) : null}
    </div>
  ) : (
    <div className="space-y-4">
      {uploader.rows.length > 0 ? (
        <ul className="space-y-2" aria-label="Caricamenti in corso">
          {uploader.rows.map((row) => (
            <li key={row.key}>
              <Sending row={row} onDismiss={() => uploader.dismiss(row.key)} />
            </li>
          ))}
        </ul>
      ) : null}

      {chosen.length > 0 && bulkable && wide ? (
        <div
          role="toolbar"
          aria-label="Azioni sui documenti scelti"
          className="flex flex-wrap items-center gap-3 border bg-card px-4 py-2 text-sm"
        >
          <span className="font-medium">
            {chosen.length === 1 ? '1 scelto' : `${chosen.length} scelti`}
          </span>
          <Button variant="outline" size="sm" onClick={() => setBulk('discard')}>
            Scarta…
          </Button>
          {legal_entities.length > 0 ? (
            <Button variant="outline" size="sm" onClick={() => setBulk('company')}>
              Assegna all’azienda…
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => setSelected([])}>
            Togli la scelta
          </Button>
        </div>
      ) : null}

      {wide ? (
        <div className="overflow-x-auto overflow-y-hidden border bg-card">
          <Table scrollLabel="Documenti in arrivo">
            <TableHeader>
              <TableRow>
                {bulkable ? (
                  <TableHead className="w-10">
                    <Checkbox
                      aria-label="Scegli tutti i documenti"
                      checked={items.length > 0 && chosen.length === items.length}
                      onCheckedChange={(on) =>
                        setSelected(on === true ? items.map((candidate) => candidate.id) : [])
                      }
                    />
                  </TableHead>
                ) : null}
                <TableHead>Documento</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead className="text-right">Importo</TableHead>
                <TableHead>Stato</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={row.id === item ? 'selected' : undefined}
                  aria-selected={row.id === item}
                  tabIndex={0}
                  className="cursor-pointer align-top focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                  onClick={() => onItemChange(row.id)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key !== 'Enter' && event.key !== ' ') return
                    event.preventDefault()
                    onItemChange(row.id)
                  }}
                >
                  {bulkable ? (
                    <TableCell onClick={(event) => event.stopPropagation()}>
                      <Checkbox
                        aria-label={`Scegli ${row.original_name}`}
                        checked={chosen.includes(row.id)}
                        onCheckedChange={() => toggle(row.id)}
                      />
                    </TableCell>
                  ) : null}
                  <TableCell>
                    <Document item={row} me={me} admin={admin} />
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{kindWord(row)}</TableCell>
                  <TableCell className="whitespace-nowrap text-right tabular-nums">
                    {amountOf(row)}
                  </TableCell>
                  <TableCell>
                    <StateCell item={row} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Documenti in arrivo">
          {items.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => onItemChange(row.id)}
                className="block min-h-24 w-full space-y-2 border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Document item={row} me={me} admin={admin} />
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span>{kindWord(row)}</span>
                  <span className="tabular-nums">{amountOf(row)}</span>
                </div>
                <StateCell item={row} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {list.hasMore ? (
        <LoadMore
          label={`Mostra altri (${list.total} in tutto)`}
          message={`Mostrati ${list.items.length} di ${documents(list.total)}.`}
          isFetchingMore={list.isFetchingMore}
          hasError={false}
          error={null}
          onLoadMore={list.fetchMore}
        />
      ) : null}
    </div>
  )

  return (
    <Tabs
      value={tab}
      onValueChange={(next) => {
        setSelected([])
        onTabChange(next as InboxTab)
      }}
      onDragOver={(event: DragEvent) => {
        if (!canWrite || !event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={(event: DragEvent) => {
        if (event.currentTarget === event.target) setDragging(false)
      }}
      onDrop={dropped}
      className={cn(
        'flex min-h-full flex-col',
        // From `lg` the page is as tall as the window and its list and panel scroll inside it, so the
        // panel's acts are always in view; below it the page flows and the window scrolls.
        inline && 'h-full',
        dragging && 'outline-2 -outline-offset-2 outline-dashed outline-ring',
      )}
    >
      {header}
      {canWrite ? (
        <input
          ref={picker}
          type="file"
          multiple
          accept={ACCEPT}
          aria-label="Scegli i file da caricare"
          className="sr-only"
          onChange={(event) => {
            pick(event.target.files)
            event.target.value = ''
          }}
        />
      ) : null}
      <div className={cn('flex gap-6 px-8 pb-6 pt-2', inline ? 'min-h-0 flex-1' : 'items-start')}>
        <div className={cn('min-w-0 flex-1', inline && 'h-full overflow-y-auto')}>{body}</div>
        {item !== null && inline ? (
          <aside
            aria-label="Documento aperto"
            className="flex h-full w-82.5 shrink-0 flex-col overflow-hidden border bg-card"
          >
            <div className="flex justify-end border-b px-2 py-1">
              <Button variant="ghost" size="sm" onClick={() => onItemChange(null)}>
                Chiudi
              </Button>
            </div>
            <InboxPanel id={item} onOpen={onItemChange} onClose={() => onItemChange(null)} />
          </aside>
        ) : null}
      </div>

      {item !== null && !inline ? (
        <Sheet open onOpenChange={(open) => !open && onItemChange(null)}>
          <SheetContent
            side="right"
            closeLabel="Chiudi"
            className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none"
          >
            <SheetTitle className="sr-only">Documento aperto</SheetTitle>
            <SheetDescription className="sr-only">
              Il file, da dove arriva, cosa è stato letto e cosa si può fare.
            </SheetDescription>
            <div className="flex min-h-0 flex-1 flex-col pt-12">
              <InboxPanel id={item} onOpen={onItemChange} onClose={() => onItemChange(null)} />
            </div>
          </SheetContent>
        </Sheet>
      ) : null}

      {bulk === 'discard' ? (
        <DiscardDialog
          count={chosen.length}
          withProposal={items.some((candidate) => chosen.includes(candidate.id) && candidate.state === 'proposed')}
          onClose={() => setBulk(null)}
          onConfirm={(reason) =>
            forEach(chosen, (id) => discard.mutateAsync({ id, reason }), 'scartati')
          }
        />
      ) : null}
      {bulk === 'company' ? (
        <CompanyDialog
          count={chosen.length}
          onClose={() => setBulk(null)}
          onConfirm={(legalEntityId) =>
            forEach(
              chosen,
              (id) => assign.mutateAsync({ id, legal_entity_id: legalEntityId }),
              'assegnati',
            )
          }
        />
      ) : null}
    </Tabs>
  )
}
