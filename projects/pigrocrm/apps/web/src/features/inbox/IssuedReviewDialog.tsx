import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { ResultPanel, ReviewPanel } from '@/features/invoices/ImportReview'
import {
  countConfirmed,
  countWrites,
  decisionsFor,
  plural,
  type ChosenOrganization,
  type ConfirmRow,
} from '@/features/invoices/importPlan'
import { useInvalidateAfterImport } from '@/features/invoices/importQueries'
import { api, toProblem, unwrap, type ProblemDetail } from '@/lib/api'
import { useLegalEntity } from '@/lib/legalEntity'
import { useAfterUpload } from './queries'
import type { InboxItem } from './labels'

/**
 * The person's confirmation of a sales invoice that waits in «In arrivo» (design § 3, first
 * rule; REB-972). It is the review the Fatture page's «Importa» runs, on this item's own file:
 * it reads what the import would do with each invoice and writes nothing until «Importa». Writing
 * consumes a register number and may create customers, so it is an administrator's and a person's,
 * and the item becomes «Archiviato» only once every invoice of the file is on the register.
 */
export function IssuedReviewDialog({ item, onClose }: { item: InboxItem; onClose: () => void }) {
  const legalEntity = useLegalEntity()
  const refresh = useAfterUpload()
  const invalidateImport = useInvalidateAfterImport()
  const review = useQuery({
    // Outside the `inbox` prefix a write invalidates: the item is `filed` once imported, and a
    // second read of its review would answer 409 under the result. Read once per opening.
    queryKey: ['issued-review', item.id],
    gcTime: 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    queryFn: () =>
      unwrap(
        api.GET('/api/inbox/items/{item_id}/issued-review', {
          params: { path: { item_id: item.id } },
        }),
      ),
  })
  const [declined, setDeclined] = useState<ReadonlySet<string>>(new Set())
  const [chosen, setChosen] = useState<ReadonlyMap<string, ChosenOrganization>>(new Map())
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<ConfirmRow[] | null>(null)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const rows = review.data?.lines ?? []
  const writes = countWrites(rows, declined)

  async function confirm() {
    setConfirming(true)
    setProblem(null)
    try {
      const body = decisionsFor(rows, declined, new Map(), chosen)
      const written = await unwrap(
        api.POST('/api/inbox/items/{item_id}/issued-import', {
          params: { path: { item_id: item.id } },
          // The generated body types the server's defaults as required; an omitted flag is false.
          body: body as never,
        }),
      )
      setResult(written.lines)
      const imported = countConfirmed(written.lines).imported ?? 0
      if (imported > 0) toast.success(plural(imported, 'fattura importata', 'fatture importate'))
    } catch (error) {
      setProblem(toProblem(error))
    } finally {
      setConfirming(false)
      // Whatever happened the register may have changed, and the item with it.
      invalidateImport()
      refresh()
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && !confirming && onClose()}>
      <DialogContent className="max-h-[85vh] grid-cols-[minmax(0,1fr)] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Rivedi e importa la fattura</DialogTitle>
          <DialogDescription>
            Prima vedi cosa verrebbe creato: il registro delle fatture emesse cambia solo quando
            confermi, e la conferma è tua.
          </DialogDescription>
        </DialogHeader>

        {problem ? <QueryErrorBanner error={problem} /> : null}
        {review.isError ? <QueryErrorBanner error={review.error} /> : null}
        {review.isPending ? <LoadingState label="Leggo la fattura…" className="py-8" /> : null}

        {result === null && review.data ? (
          <ReviewPanel
            rows={rows}
            declined={declined}
            chosen={chosen}
            onChoose={(key, customer) =>
              setChosen((previous) => {
                const next = new Map(previous)
                if (customer === undefined) next.delete(key)
                else next.set(key, customer)
                return next
              })
            }
            onToggleParty={(key) =>
              setDeclined((previous) => {
                const next = new Set(previous)
                if (next.has(key)) next.delete(key)
                else next.add(key)
                return next
              })
            }
          />
        ) : null}
        {result ? (
          <ResultPanel
            rows={result}
            interrupted={false}
            legalEntityName={(id) =>
              legalEntity.several ? legalEntity.byId(id)?.legal_name : undefined
            }
          />
        ) : null}

        <DialogFooter>
          {result ? (
            <Button onClick={onClose}>Chiudi</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={onClose} disabled={confirming}>
                Annulla
              </Button>
              <Button onClick={() => void confirm()} disabled={confirming || writes === 0}>
                {confirming ? <Loader className="mr-2 size-4" /> : null}
                {writes === 0
                  ? 'Niente da importare'
                  : `Importa ${plural(writes, 'fattura', 'fatture')}`}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
