import { useState } from 'react'
import { FilterRow } from '@/components/FilterRow'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { useAuth } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { Button } from '@rebase/ui/button'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { ExportPanel } from './ExportPanel'
import {
  CONTROL_TONES,
  STATUS_LABELS,
  STATUS_TONES,
  controlLabel,
  formatDateTime,
  partCount,
  partsWord,
  readIndex,
  statusOf,
  type Attempt,
  type StoredExport,
} from './labels'
import { periodName } from './periods'
import { useBuilderNames, useCompanyName, useExportList } from './queries'

/** The table from `md` up, the inline panel from `lg`: below them rows are cards and the panel
 *  is a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

const ANY = 'all'
const YEARS_SHOWN = 5

const EMPTY_TEXT =
  'Qui trovi gli archivi creati per il commercialista: da qui li scarichi. Per crearne uno, scegli l’azienda e il periodo.'

/** The name of a company on a row: ellipsised at the column's width, the whole name on hover. */
function Company({ name }: { name: string }) {
  return (
    <p className="max-w-72 truncate font-medium" title={name}>
      {name}
    </p>
  )
}

function StatusCell({ item }: { item: StoredExport }) {
  const status = statusOf(item)
  return (
    <div className="space-y-1">
      <StatusPill tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</StatusPill>
      {status === 'deleted' ? (
        <p className="text-xs text-muted-foreground">Oltre i 13 mesi</p>
      ) : null}
    </div>
  )
}

function ControlCell({ item }: { item: StoredExport }) {
  if (statusOf(item) !== 'ready') return null
  const open = readIndex(item.checklist).missing.length
  const state = open === 0 ? 'complete' : 'open'
  return <StatusPill tone={CONTROL_TONES[state]}>{controlLabel(state, open)}</StatusPill>
}

function Building() {
  return (
    <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
      <Loader className="size-4 shrink-0" />
      Preparo l’esportazione…
    </div>
  )
}

function AttemptState({
  attempt,
  onRetry,
  onDismiss,
}: {
  attempt: Attempt
  onRetry: () => void
  onDismiss: () => void
}) {
  const status = attempt.status
  return (
    <div className="max-w-80 space-y-1">
      <StatusPill tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</StatusPill>
      {status === 'building' ? (
        <Building />
      ) : (
        <>
          <p role="alert" className="whitespace-normal text-xs text-destructive">
            {attempt.reason}
          </p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={onRetry}>
              Riprova
            </Button>
            <Button size="sm" variant="ghost" onClick={onDismiss}>
              Chiudi
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

/**
 * «Esportazioni»: the stored exports as a gridded table with the open one in a panel beside it
 * from `lg`, cards and a full-screen sheet below. 50 rows at a time, filtered by company (when
 * the space has several) and year. A build in progress or refused is a row of its own at the top.
 */
export function ExportsTab({
  item,
  attempts,
  onItemChange,
  onRetry,
  onDismiss,
  onPrepare,
}: {
  item: string | null
  attempts: readonly Attempt[]
  onItemChange: (next: string | null) => void
  onRetry: (attempt: Attempt) => void
  onDismiss: (attempt: Attempt) => void
  onPrepare: () => void
}) {
  const { user } = useAuth()
  const me = user?.id ?? null
  const wide = useMediaQuery(TABLE)
  const inline = useMediaQuery(INLINE_PANEL)
  const { legal_entities, several } = useLegalEntity()
  const [company, setCompany] = useState<string | null>(null)
  const [year, setYear] = useState<number | null>(null)
  const list = useExportList({ legalEntityId: company, year })
  const companyName = useCompanyName()
  const builder = useBuilderNames()

  const items = list.data?.pages.flatMap((page) => page.items) ?? []
  const total = list.data?.pages.at(-1)?.total_count ?? 0
  const filtered = company !== null || year !== null
  const thisYear = new Date().getFullYear()
  const years = Array.from({ length: YEARS_SHOWN }, (_, back) => thisYear - back)

  const filters = (
    <FilterRow className="grid grid-cols-2 sm:flex sm:flex-wrap">
      {several ? (
        <Select
          value={company ?? ANY}
          onValueChange={(next) => setCompany(next === ANY ? null : next)}
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
      <Select
        value={year === null ? ANY : String(year)}
        onValueChange={(next) => setYear(years.find((candidate) => String(candidate) === next) ?? null)}
      >
        <SelectTrigger aria-label="Anno" className="w-full sm:w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>Anno</SelectItem>
          {years.map((candidate) => (
            <SelectItem key={candidate} value={String(candidate)}>
              {candidate}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FilterRow>
  )

  const subtitle = (row: StoredExport) =>
    `${periodName(row.period_from, row.period_to)} · ${partsWord(partCount(readIndex(row.checklist)))}`

  const body = list.isError ? (
    <div className="space-y-3">
      <QueryErrorBanner error={list.error} onRetry={() => void list.refetch()} />
    </div>
  ) : list.isPending ? (
    <LoadingState label="Leggo le esportazioni…" className="py-8" />
  ) : items.length === 0 && attempts.length === 0 ? (
    <div className="border border-dashed px-6 py-12 text-center">
      <p className="text-base font-semibold">{filtered ? 'Nessuna esportazione qui' : 'Nessuna esportazione'}</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
        {filtered ? 'Nessuna esportazione corrisponde ai filtri.' : EMPTY_TEXT}
      </p>
      {filtered ? null : (
        <Button className="mt-4" onClick={onPrepare}>
          Prepara un’esportazione
        </Button>
      )}
    </div>
  ) : (
    <div className="space-y-4">
      {wide ? (
        <div className="overflow-x-auto overflow-y-hidden border bg-card">
          <Table scrollLabel="Esportazioni">
            <TableHeader>
              <TableRow>
                <TableHead>Azienda e periodo</TableHead>
                <TableHead>Creata</TableHead>
                <TableHead>Controllo</TableHead>
                <TableHead>Stato</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {attempts.map((attempt) => (
                <TableRow key={attempt.key} className="align-top">
                  <TableCell>
                    <Company name={attempt.target.company} />
                    <p className="text-xs text-muted-foreground">{attempt.target.period}</p>
                  </TableCell>
                  <TableCell />
                  <TableCell />
                  <TableCell>
                    <AttemptState
                      attempt={attempt}
                      onRetry={() => onRetry(attempt)}
                      onDismiss={() => onDismiss(attempt)}
                    />
                  </TableCell>
                </TableRow>
              ))}
              {items.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={row.id === item ? 'selected' : undefined}
                  aria-selected={row.id === item}
                  tabIndex={0}
                  className={cn(
                    'cursor-pointer align-top focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                    statusOf(row) === 'deleted' && 'text-muted-foreground',
                  )}
                  onClick={() => onItemChange(row.id)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return
                    if (event.key !== 'Enter' && event.key !== ' ') return
                    event.preventDefault()
                    onItemChange(row.id)
                  }}
                >
                  <TableCell>
                    <Company name={companyName(row)} />
                    <p className="text-xs text-muted-foreground">{subtitle(row)}</p>
                  </TableCell>
                  <TableCell className="whitespace-nowrap tabular-nums">
                    {formatDateTime(row.built_at)}
                    <p className="text-xs text-muted-foreground">{builder(row.built_by, me).name}</p>
                  </TableCell>
                  <TableCell>
                    <ControlCell item={row} />
                  </TableCell>
                  <TableCell>
                    <StatusCell item={row} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Esportazioni">
          {attempts.map((attempt) => (
            <li key={attempt.key} className="space-y-2 border bg-card p-4">
              <Company name={attempt.target.company} />
              <p className="text-xs text-muted-foreground">{attempt.target.period}</p>
              <AttemptState
                attempt={attempt}
                onRetry={() => onRetry(attempt)}
                onDismiss={() => onDismiss(attempt)}
              />
            </li>
          ))}
          {items.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => onItemChange(row.id)}
                className={cn(
                  'block min-h-24 w-full space-y-2 border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  statusOf(row) === 'deleted' && 'text-muted-foreground',
                )}
              >
                <Company name={companyName(row)} />
                <p className="text-xs text-muted-foreground">{subtitle(row)}</p>
                <p className="text-xs tabular-nums text-muted-foreground">
                  {formatDateTime(row.built_at)} · {builder(row.built_by, me).name}
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusCell item={row} />
                  <ControlCell item={row} />
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {list.hasNextPage ? (
        <LoadMore
          label="Mostra altri"
          message={`Mostrate ${items.length} di ${total} esportazioni.`}
          isFetchingMore={list.isFetchingNextPage}
          hasError={false}
          error={null}
          onLoadMore={() => void list.fetchNextPage()}
        />
      ) : null}
    </div>
  )

  return (
    <>
      {filters}
      <div className={cn('flex gap-6 px-8 pb-6 pt-2', inline ? 'min-h-0 flex-1' : 'items-start')}>
        <div className={cn('min-w-0 flex-1', inline && 'h-full overflow-y-auto')}>{body}</div>
        {item !== null && inline ? (
          <aside
            aria-label="Esportazione aperta"
            className="flex h-full w-82.5 shrink-0 flex-col overflow-hidden border bg-card"
          >
            <div className="flex justify-end border-b px-2 py-1">
              <Button variant="ghost" size="sm" onClick={() => onItemChange(null)}>
                Chiudi
              </Button>
            </div>
            <ExportPanel id={item} onClose={() => onItemChange(null)} />
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
            <SheetTitle className="sr-only">Esportazione aperta</SheetTitle>
            <SheetDescription className="sr-only">
              Cosa c’è nello ZIP, il controllo di quando è stata creata e come scaricarla.
            </SheetDescription>
            <div className="flex min-h-0 flex-1 flex-col pt-12">
              <ExportPanel id={item} onClose={() => onItemChange(null)} />
            </div>
          </SheetContent>
        </Sheet>
      ) : null}
    </>
  )
}
