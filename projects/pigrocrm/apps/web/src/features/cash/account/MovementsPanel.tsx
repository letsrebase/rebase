import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { formatIsoDateItalian } from '@/lib/dates'
import { moneyIn } from '../money'
import { movementPill, periodLabel, statementFormatLabel } from './labels'
import { useMovements, type BankAccount, type Movement, type MovementFilter, type Statement } from './queries'

const ALL_STATEMENTS = 'all'

/** The two chips that filter the queue: «Da abbinare» with the server's count, and «Tutti». */
export function FilterChips({
  filter,
  unmatchedCount,
  onChange,
}: {
  filter: MovementFilter
  unmatchedCount: number
  onChange: (next: MovementFilter) => void
}) {
  const chips: Array<{ value: MovementFilter; label: string }> = [
    { value: 'unmatched', label: `Da abbinare (${unmatchedCount})` },
    { value: 'all', label: 'Tutti' },
  ]
  return (
    <div role="group" aria-label="Filtro dei movimenti" className="flex flex-wrap gap-2">
      {chips.map((chip) => (
        <button
          key={chip.value}
          type="button"
          aria-pressed={filter === chip.value}
          onClick={() => onChange(chip.value)}
          className={cn(
            'border px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            filter === chip.value ? 'border-foreground bg-foreground text-background' : 'bg-card',
          )}
        >
          {chip.label}
        </button>
      ))}
    </div>
  )
}

function StatementSelect({
  statements,
  value,
  onChange,
}: {
  statements: readonly Statement[]
  value: string | null
  onChange: (next: string | null) => void
}) {
  return (
    <Select value={value ?? ALL_STATEMENTS} onValueChange={(next) => onChange(next === ALL_STATEMENTS ? null : next)}>
      <SelectTrigger aria-label="Estratto conto" className="w-full sm:w-80">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_STATEMENTS}>Tutti gli estratti conto</SelectItem>
        {statements.map((statement) => (
          <SelectItem key={statement.id} value={statement.id}>
            {periodLabel(statement.period_from, statement.period_to)} · {statementFormatLabel(statement.format)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** The bank's own words, verbatim, with who it names under them. */
function Description({ movement }: { movement: Movement }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <p className="break-words">{movement.description ?? '—'}</p>
      {movement.counterparty_name !== null && (
        <p className="break-words text-xs text-muted-foreground">{movement.counterparty_name}</p>
      )}
      {movement.state === 'ignored' && movement.ignored_reason !== null && (
        <p className="break-words text-xs text-muted-foreground">{movement.ignored_reason}</p>
      )}
    </div>
  )
}

function MovementPill({ movement }: { movement: Movement }) {
  const pill = movementPill(movement)
  return <StatusPill tone={pill.tone}>{pill.label}</StatusPill>
}

function MovementsTable({ items, currencyOf }: { items: readonly Movement[]; currencyOf: string }) {
  return (
    <div className="overflow-x-auto overflow-y-hidden border bg-card">
      <Table scrollLabel="Movimenti del conto">
        <TableHeader>
          <TableRow>
            <TableHead className="w-28">Data</TableHead>
            <TableHead>Descrizione</TableHead>
            <TableHead className="text-right">Importo</TableHead>
            <TableHead>Stato</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((movement) => (
            <TableRow key={movement.id} className="align-top">
              <TableCell className="whitespace-nowrap tabular-nums">
                {formatIsoDateItalian(movement.booking_date)}
              </TableCell>
              <TableCell className="max-w-xl whitespace-normal">
                <Description movement={movement} />
              </TableCell>
              <TableCell className="whitespace-nowrap text-right tabular-nums">
                {moneyIn(movement.amount, movement.currency || currencyOf)}
              </TableCell>
              <TableCell>
                <MovementPill movement={movement} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function MovementCards({ items, currencyOf }: { items: readonly Movement[]; currencyOf: string }) {
  return (
    <ul aria-label="Movimenti del conto" className="space-y-3">
      {items.map((movement) => (
        <li key={movement.id} className="space-y-2 border bg-card p-4">
          <p className="text-lg font-semibold tabular-nums">
            {moneyIn(movement.amount, movement.currency || currencyOf)}
          </p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="tabular-nums">{formatIsoDateItalian(movement.booking_date)}</span>
            <MovementPill movement={movement} />
          </div>
          <div className="text-sm">
            <Description movement={movement} />
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * The queue of movements across the account's statements, newest booking first, 50 at a time:
 * a read-only list with the state each one is in. Matching is not on this page yet, so a row
 * has no action.
 */
export function MovementsPanel({
  account,
  filter,
  onFilterChange,
  statementId,
  onStatementChange,
  statements,
  statementCount,
  wide,
  canUpload,
  onUpload,
}: {
  account: BankAccount
  filter: MovementFilter
  onFilterChange: (next: MovementFilter) => void
  statementId: string | null
  onStatementChange: (next: string | null) => void
  statements: readonly Statement[]
  /** The server's count of the account's statements; undefined until it answered. */
  statementCount: number | undefined
  /** From 768 px the chips are drawn here; below it the page's segmented control carries them. */
  wide: boolean
  canUpload: boolean
  onUpload: () => void
}) {
  const movements = useMovements(account.id, filter, statementId)

  const empty = !movements.isLoading && !movements.isError && movements.items.length === 0
  let sentence = 'Nessun movimento.'
  let offersUpload = false
  if (statementId !== null) {
    sentence =
      filter === 'all' ? 'Questo estratto non ha movimenti.' : 'Nessun movimento da abbinare in questo estratto.'
  } else if (statementCount === 0) {
    sentence = 'Carica il primo estratto conto per vedere i movimenti.'
    offersUpload = canUpload
  } else if (filter === 'unmatched') {
    sentence = 'Nessun movimento da abbinare.'
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        {wide && <FilterChips filter={filter} unmatchedCount={account.unmatched_count} onChange={onFilterChange} />}
        {statements.length > 1 && (
          <StatementSelect statements={statements} value={statementId} onChange={onStatementChange} />
        )}
      </div>

      {movements.isLoading ? (
        <LoadingState label="Leggo i movimenti…" />
      ) : movements.isError ? (
        <QueryErrorBanner error={movements.error} onRetry={movements.retry} />
      ) : empty ? (
        <div className="space-y-3">
          <p role="status" className="text-sm text-muted-foreground">
            {sentence}
          </p>
          {offersUpload && <Button onClick={onUpload}>Carica estratto conto</Button>}
        </div>
      ) : wide ? (
        <MovementsTable items={movements.items} currencyOf={account.currency} />
      ) : (
        <MovementCards items={movements.items} currencyOf={account.currency} />
      )}

      {movements.hasMore && (
        <LoadMore
          label="Mostra altri"
          message={
            movements.totalCount === undefined
              ? 'Ci sono altri movimenti.'
              : `Mostrati ${movements.items.length} di ${movements.totalCount} movimenti.`
          }
          isFetchingMore={movements.isFetchingMore}
          hasError={false}
          error={null}
          onLoadMore={movements.loadMore}
        />
      )}
    </div>
  )
}
