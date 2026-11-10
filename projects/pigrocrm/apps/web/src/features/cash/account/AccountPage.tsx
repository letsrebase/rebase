import { useParams } from '@tanstack/react-router'
import { Landmark } from 'lucide-react'
import { useState, type CSSProperties } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { PHONE_BAR_HEIGHT } from '@/components/PhoneBar'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { useAuth, useCan } from '@/lib/auth'
import { roleLabel } from '@/lib/roles'
import { AccountCard, ArrivalCard, BalanceCard, CoverageStrip } from './AccountCards'
import { AccountDialog } from './AccountDialog'
import { MovementsPanel } from './MovementsPanel'
import { StatementsPanel } from './StatementsPanel'
import { UploadStatementDialog } from './UploadStatementDialog'
import { useBankAccounts, useCoverage, useStatements, type MovementFilter } from './queries'

/** From here the page has its three cards, its table and its tabs; below it, one column of cards. */
const WIDE = '(min-width: 768px)'

type View = 'movements' | 'statements'
type Segment = MovementFilter | 'statements'

/**
 * One bank account (REB-1025, the approved brief `docs/design/2026-11-10-cash-and-schedule-brief.md`,
 * «The account page» and «A statement's movements»): its balance and the date it is true as of,
 * how its statements arrive, whether every month has one, and the queue of what was read from
 * them. Everything on it is an administrator's: any other role gets one sentence and no request
 * is made for an account.
 */
export function AccountPage() {
  const { accountId } = useParams({ from: '/app/cash/accounts/$accountId' })
  const { user } = useAuth()
  const allowed = useCan('list_bank_movements')

  if (user === null) return null
  if (!allowed) {
    return (
      <>
        <PageHeader icon={Landmark} title="Conto" />
        <div className="px-8 pb-6 pt-1">
          <p role="note" className="text-sm text-muted-foreground">
            Il tuo ruolo in questo spazio è «{roleLabel(user.role)}»: i conti bancari sono riservati
            all’«{roleLabel('admin')}».
          </p>
        </div>
      </>
    )
  }
  return <AccountBody accountId={accountId} />
}

function AccountBody({ accountId }: { accountId: string }) {
  const wide = useMediaQuery(WIDE)
  const accounts = useBankAccounts()
  const coverage = useCoverage(accountId)
  const statements = useStatements(accountId)
  const [view, setView] = useState<View>('movements')
  const [filter, setFilter] = useState<MovementFilter>('unmatched')
  const [statementId, setStatementId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [editing, setEditing] = useState(false)

  if (accounts.isLoading) {
    return <LoadingState label="Leggo il conto…" className="p-8" />
  }
  if (accounts.isError) {
    return (
      <div className="p-8">
        <QueryErrorBanner error={accounts.error} onRetry={() => void accounts.refetch()} />
      </div>
    )
  }
  const account = accounts.data?.find((candidate) => candidate.id === accountId)
  if (account === undefined) {
    return (
      <>
        <PageHeader icon={Landmark} title="Conto" />
        <p role="status" className="px-8 pb-6 pt-1 text-sm text-muted-foreground">
          Questo conto non esiste.
        </p>
      </>
    )
  }

  const archived = account.archived_at !== null
  const canUpload = !archived
  const segment: Segment = view === 'statements' ? 'statements' : filter
  const readable = statements.items.filter((statement) => statement.state !== 'reverted')
  // A filter on a statement that is no longer listed (reverted) or that has no sibling to be
  // told from (the select is not drawn for one) would be invisible and stuck: it lapses.
  const activeStatement =
    statementId !== null && readable.length > 1 && readable.some((statement) => statement.id === statementId)
      ? statementId
      : null

  function showStatement(id: string) {
    setStatementId(id)
    setFilter('all')
    setView('movements')
  }

  function pickSegment(next: Segment) {
    if (next === 'statements') {
      setView('statements')
      return
    }
    setView('movements')
    setFilter(next)
  }

  const movements = (
    <MovementsPanel
      account={account}
      filter={filter}
      onFilterChange={setFilter}
      statementId={activeStatement}
      onStatementChange={setStatementId}
      statements={readable}
      statementCount={statements.totalCount}
      wide={wide}
      canUpload={canUpload}
      onUpload={() => setUploading(true)}
    />
  )
  const statementsPanel = (
    <StatementsPanel
      accountId={account.id}
      coverage={coverage.data}
      wide={wide}
      canUpload={canUpload}
      onShowMovements={showStatement}
      onUpload={() => setUploading(true)}
    />
  )
  const strip = coverage.isError ? (
    <QueryErrorBanner error={coverage.error} onRetry={() => void coverage.refetch()} />
  ) : coverage.data === undefined ? (
    <LoadingState label="Leggo gli estratti conto…" />
  ) : (
    <CoverageStrip coverage={coverage.data} wide={wide} />
  )

  return (
    <>
      <PageHeader
        icon={Landmark}
        title={account.name}
        description={`Cassa · ${account.bank} · ${account.legal_entity_name}`}
        actions={
          <>
            <Button variant="outline" onClick={() => setEditing(true)}>
              Modifica il conto
            </Button>
            {wide && canUpload && <Button onClick={() => setUploading(true)}>Carica estratto conto</Button>}
          </>
        }
      />
      <div className="space-y-6 px-8 pb-6 pt-1">
        {archived && (
          <p role="note" className="border bg-muted/40 px-3 py-2 text-sm">
            Questo conto è archiviato: i movimenti restano.
          </p>
        )}

        {wide ? (
          <div className="grid gap-4 md:grid-cols-3">
            <BalanceCard account={account} />
            <AccountCard account={account} />
            <ArrivalCard account={account} />
          </div>
        ) : (
          <BalanceCard account={account} />
        )}

        {strip}

        {wide ? (
          <Tabs value={view} onValueChange={(next) => setView(next as View)}>
            <TabsList>
              <TabsTrigger value="movements">Movimenti</TabsTrigger>
              <TabsTrigger value="statements">Estratti conto</TabsTrigger>
            </TabsList>
            <TabsContent value="movements" className="pt-4">
              {movements}
            </TabsContent>
            <TabsContent value="statements" className="pt-4">
              {statementsPanel}
            </TabsContent>
          </Tabs>
        ) : (
          <div className="space-y-4">
            <div role="group" aria-label="Che cosa vedere" className="grid grid-cols-3 border">
              {(
                [
                  ['unmatched', 'Da abbinare'],
                  ['all', 'Tutti'],
                  ['statements', 'Estratti'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={segment === value}
                  onClick={() => pickSegment(value)}
                  className={cn(
                    'px-2 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    segment === value ? 'bg-foreground text-background' : 'bg-card',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            {view === 'statements' ? statementsPanel : movements}
            <AccountCard account={account} />
            <ArrivalCard account={account} />
          </div>
        )}
      </div>

      {!wide && canUpload && (
        <div
          style={{ '--phone-bar': `calc(${PHONE_BAR_HEIGHT} + env(safe-area-inset-bottom))` } as CSSProperties}
          className="sticky bottom-(--phone-bar) z-10 border-t bg-background px-4 py-3"
        >
          <Button className="w-full" onClick={() => setUploading(true)}>
            Carica estratto conto
          </Button>
        </div>
      )}

      <UploadStatementDialog open={uploading} onOpenChange={setUploading} accountId={account.id} />
      <AccountDialog open={editing} onOpenChange={setEditing} account={account} />
    </>
  )
}
