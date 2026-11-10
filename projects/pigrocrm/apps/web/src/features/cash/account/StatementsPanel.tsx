import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { toProblem, type ProblemDetail } from '@/lib/api'
import {
  COVERAGE_STATE_LABELS,
  COVERAGE_STATE_TONES,
  REVERT_SENTENCE,
  STATEMENT_STATE_LABELS,
  STATEMENT_STATE_TONES,
  checkSentence,
  movementsCount,
  monthTitle,
  periodLabel,
  statementFormatLabel,
} from './labels'
import { useRevertStatement, useStatements, type AccountCoverage, type CoverageMonth, type Statement } from './queries'

type Row =
  | { kind: 'statement'; key: string; sortKey: string; statement: Statement }
  | { kind: 'missing'; key: string; sortKey: string; month: CoverageMonth }

/** The statements and the months that should have one and have none, newest period first (the
 *  ISO dates sort as the strings they are). */
function rowsOf(statements: readonly Statement[], coverage: AccountCoverage | undefined): Row[] {
  const rows: Row[] = statements.map((statement) => ({
    kind: 'statement',
    key: statement.id,
    sortKey: statement.period_to,
    statement,
  }))
  for (const month of coverage?.months ?? []) {
    if (month.state === 'missing') {
      rows.push({ kind: 'missing', key: `missing-${month.month}`, sortKey: month.period_to, month })
    }
  }
  return rows.sort((a, b) => (a.sortKey < b.sortKey ? 1 : a.sortKey > b.sortKey ? -1 : 0))
}

/** «Annulla l'importazione», after a second look: it removes what the statement brought. */
function RevertDialog({ statement, onClose }: { statement: Statement | null; onClose: () => void }) {
  const revert = useRevertStatement()
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [shown, setShown] = useState<Statement | null>(null)
  if (statement !== shown) {
    setShown(statement)
    setProblem(null)
    revert.reset()
  }
  return (
    <Dialog open={statement !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Annulla l’importazione</DialogTitle>
          <DialogDescription>
            {statement === null ? '' : `${periodLabel(statement.period_from, statement.period_to)}. `}
            {REVERT_SENTENCE}.
          </DialogDescription>
        </DialogHeader>
        {problem !== null && (
          <p role="alert" className="text-sm text-destructive">
            {problem.detail}
          </p>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Non annullare
          </Button>
          <Button
            disabled={revert.isPending}
            onClick={() => {
              if (statement === null) return
              setProblem(null)
              revert.mutate(statement.id, {
                onSuccess: () => {
                  toast.success('Importazione annullata')
                  onClose()
                },
                onError: (error) => setProblem(toProblem(error)),
              })
            }}
          >
            {revert.isPending ? 'Annullo…' : 'Annulla l’importazione'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatementCells({ statement }: { statement: Statement }) {
  const check = checkSentence(statement.check)
  return (
    <div className="min-w-0 space-y-0.5 whitespace-normal">
      <p className="font-medium tabular-nums">{periodLabel(statement.period_from, statement.period_to)}</p>
      <p className="text-xs text-muted-foreground">{statementFormatLabel(statement.format)}</p>
      {check !== null && <p className="text-xs text-destructive">{check}</p>}
    </div>
  )
}

function StatementActions({
  statement,
  onShow,
  onRevert,
}: {
  statement: Statement
  onShow: (id: string) => void
  onRevert: (statement: Statement) => void
}) {
  if (statement.state !== 'imported') return null
  return (
    <div className="flex flex-col items-start gap-1 whitespace-normal">
      <Button variant="outline" size="sm" onClick={() => onShow(statement.id)}>
        Vedi i movimenti
      </Button>
      <Button variant="ghost" size="sm" onClick={() => onRevert(statement)}>
        Annulla l’importazione
      </Button>
      <p className="max-w-56 text-xs text-muted-foreground">{REVERT_SENTENCE}</p>
    </div>
  )
}

function MissingTitle({ month }: { month: CoverageMonth }) {
  return <p className="font-medium">{monthTitle(month.period_from)}</p>
}

/**
 * The account's statements, each with the check the server ran on its stated balances, and the
 * months that should have one and have none, as rows with «Manca» and «Carica». A row filters
 * the movements to its statement: there is no page of a statement.
 */
export function StatementsPanel({
  accountId,
  coverage,
  wide,
  canUpload,
  onShowMovements,
  onUpload,
}: {
  accountId: string
  coverage: AccountCoverage | undefined
  wide: boolean
  canUpload: boolean
  onShowMovements: (statementId: string) => void
  onUpload: () => void
}) {
  const statements = useStatements(accountId)
  const [reverting, setReverting] = useState<Statement | null>(null)

  if (statements.isLoading) return <LoadingState label="Leggo gli estratti conto…" />
  if (statements.isError) return <QueryErrorBanner error={statements.error} onRetry={statements.retry} />

  const rows = rowsOf(statements.items, coverage)
  if (rows.length === 0) {
    return (
      <div className="space-y-3">
        <p role="status" className="text-sm text-muted-foreground">
          Carica il primo estratto conto per vedere i movimenti.
        </p>
        {canUpload && <Button onClick={onUpload}>Carica estratto conto</Button>}
      </div>
    )
  }

  const upload =
    canUpload ? (
      <Button variant="outline" size="sm" onClick={onUpload}>
        Carica
      </Button>
    ) : null

  return (
    <div className="space-y-4">
      {wide ? (
        <div className="overflow-x-auto overflow-y-hidden border bg-card">
          <Table scrollLabel="Estratti conto">
            <TableHeader>
              <TableRow>
                <TableHead>Estratto</TableHead>
                <TableHead className="text-right">Movimenti</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead>
                  <span className="sr-only">Azioni</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) =>
                row.kind === 'statement' ? (
                  <TableRow key={row.key} className="align-top">
                    <TableCell>
                      <StatementCells statement={row.statement} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.statement.movement_count}</TableCell>
                    <TableCell>
                      <StatusPill tone={STATEMENT_STATE_TONES[row.statement.state]}>
                        {STATEMENT_STATE_LABELS[row.statement.state]}
                      </StatusPill>
                    </TableCell>
                    <TableCell>
                      <StatementActions statement={row.statement} onShow={onShowMovements} onRevert={setReverting} />
                    </TableCell>
                  </TableRow>
                ) : (
                  <TableRow key={row.key} className="align-top">
                    <TableCell>
                      <MissingTitle month={row.month} />
                    </TableCell>
                    <TableCell />
                    <TableCell>
                      <StatusPill tone={COVERAGE_STATE_TONES.missing}>{COVERAGE_STATE_LABELS.missing}</StatusPill>
                    </TableCell>
                    <TableCell>{upload}</TableCell>
                  </TableRow>
                ),
              )}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul aria-label="Estratti conto" className="space-y-3">
          {rows.map((row) =>
            row.kind === 'statement' ? (
              <li key={row.key} className="space-y-3 border bg-card p-4">
                <div className="flex items-start justify-between gap-3">
                  <StatementCells statement={row.statement} />
                  <StatusPill tone={STATEMENT_STATE_TONES[row.statement.state]}>
                    {STATEMENT_STATE_LABELS[row.statement.state]}
                  </StatusPill>
                </div>
                <p className="text-sm text-muted-foreground tabular-nums">
                  {movementsCount(row.statement.movement_count)}
                </p>
                <StatementActions statement={row.statement} onShow={onShowMovements} onRevert={setReverting} />
              </li>
            ) : (
              <li key={row.key} className="flex items-center justify-between gap-3 border bg-card p-4">
                <div className="space-y-1">
                  <MissingTitle month={row.month} />
                  <StatusPill tone={COVERAGE_STATE_TONES.missing}>{COVERAGE_STATE_LABELS.missing}</StatusPill>
                </div>
                {upload}
              </li>
            ),
          )}
        </ul>
      )}

      {statements.hasMore && (
        <LoadMore
          label="Mostra altri"
          message={
            statements.totalCount === undefined
              ? 'Ci sono altri estratti conto.'
              : `Mostrati ${statements.items.length} di ${statements.totalCount} estratti conto.`
          }
          isFetchingMore={statements.isFetchingMore}
          hasError={false}
          error={null}
          onLoadMore={statements.loadMore}
        />
      )}

      <RevertDialog statement={reverting} onClose={() => setReverting(null)} />
    </div>
  )
}
