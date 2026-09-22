import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { Handshake, List, Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Skeleton } from '@rebase/ui/skeleton'
import { DealForm } from '@/features/deals/DealForm'
import { KanbanBoard } from '@/features/deals/KanbanBoard'
import { useCreateDeal, useDeals, useMoveDeal, useStages } from '@/features/deals/queries'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

/** Shown only if `useDeals` ever exhausts its own safety valve (see that hook's
 *  `MAX_PAGES` comment) -- not a state any tenant this product is sized for
 *  should ever reach. Its whole job is to make a truncated board *visible*
 *  instead of a silently wrong column total, so it deliberately reuses the same
 *  destructive-toned banner `DynamicForm`'s own unattributed-error banner uses,
 *  rather than inventing a new "warning" visual language this design system does
 *  not otherwise have. */
function TruncatedNotice({ scope }: { scope: string }) {
  return (
    <p
      role="status"
      className="mb-4 border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
    >
      Ci sono troppi deal da mostrare tutti insieme: alcuni potrebbero mancare {scope}. Contatta un
      amministratore.
    </p>
  )
}

function DealsKanban() {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [open, setOpen] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const schema = useEntitySchema('deal')
  const stages = useStages()
  // No params: the Kanban's own per-column count and total need *every* deal
  // that exists, not a filtered subset -- see `useDeals`'s own docstring for why
  // it pages through the full result set rather than stopping at the first 50.
  const deals = useDeals()
  const move = useMoveDeal()
  const create = useCreateDeal()

  if (stages.isLoading || deals.isLoading) return <Skeleton className="m-8 h-96" />

  // `KanbanBoard` has no `DataTable` underneath it to fall back on, so it needs
  // its own version of the same check `DataTable`'s own `isError` prop makes:
  // a failed fetch with nothing usable cached must not render as "an empty
  // pipeline" (every column at 0 / 0,00 €, five "Nessun deal") -- see
  // `QueryErrorBanner`'s own docstring for the live defect this closes. Checked
  // against actual data length, not the bare `isError` flag alone, for the
  // identical reason `DataTable` does: a background refetch failing while a
  // previous, successful load is still cached should keep showing that board,
  // not replace it with a banner over one transient blip.
  const stagesUnavailable = stages.isError && (stages.data?.length ?? 0) === 0
  const dealsUnavailable = deals.isError && (deals.data?.items.length ?? 0) === 0
  const boardUnavailable = stagesUnavailable || dealsUnavailable

  return (
    <>
      <PageHeader
        icon={Handshake}
        title="Deal"
        // No filter row: the board *is* the state filter -- every deal sits in the
        // column of its own stage -- so a row of stage chips above it would say twice
        // what the columns already say.
        actions={
          <>
            <Button variant="outline" asChild>
              <Link to="/app/deal/list">
                <List className="mr-2 size-4" />
                Vista lista
              </Link>
            </Button>
            {canWrite && (
              <Button
                onClick={() => {
                  setProblem(null)
                  setOpen(true)
                }}
              >
                <Plus className="mr-2 size-4" />
                Nuovo deal
              </Button>
            )}
          </>
        }
      />

      <div className="px-8 pb-8">
        {boardUnavailable ? (
          <QueryErrorBanner error={stagesUnavailable ? stages.error : deals.error} />
        ) : (
          <>
            {deals.data?.truncated && (
              <TruncatedNotice scope="dalla board e dai totali per colonna" />
            )}

            <KanbanBoard
              stages={stages.data ?? []}
              deals={deals.data?.items ?? []}
              canDrag={canWrite}
              onOpen={(dealId) => void navigate({ to: '/app/deal/$dealId', params: { dealId } })}
              // No per-call `onError` here: `useMoveDeal` itself toasts the
              // server's message on its own mutation-level `onError`, precisely
              // so a second drag started before the first one settles cannot
              // make the first call's callback disappear -- see that hook's own
              // docstring for the `@tanstack/query-core` mechanics and the live
              // reproduction (a soft-deleted deal's card, dragged from a stale
              // board, 404s with "deal <id> not found" and the toast shows
              // exactly that).
              onMove={(dealId, stageId) => move.mutate({ dealId, stageId })}
            />
          </>
        )}
      </div>

      <DealForm
        title="Nuovo deal"
        open={open}
        onOpenChange={setOpen}
        customFields={schema.data?.custom_fields ?? []}
        problem={problem}
        busy={create.isPending}
        onSubmit={(values) => {
          setProblem(null)
          create.mutate(values, {
            onSuccess: () => {
              setOpen(false)
              toast.success('Deal creato')
            },
            onError: (error) => setProblem(toProblem(error)),
          })
        }}
      />
    </>
  )
}

export const Route = createFileRoute('/app/deal/')({ component: DealsKanban })
