import { Link } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { useCanWrite } from '@/lib/auth'
import { QUEUE_LIMIT, usePendingProposals } from './queries'

/**
 * The Home's call to action while proposals wait (REB-750): an assistant read a contract or
 * a day out of a document and nothing happens to it until somebody decides, so the number
 * is on the first screen rather than behind a menu entry. Drawn only for someone who can
 * decide (the same `useCanWrite` the buttons behind it ask, which also keeps the read from
 * being made for a reader who would never see it), and not at all while the queue is empty,
 * still loading, or could not be read: a notice that says «0 proposte» is noise, and one
 * built on a failed request would claim a count nobody has.
 */
export function PendingProposalsNotice() {
  const canWrite = useCanWrite()
  const pending = usePendingProposals({ enabled: canWrite })
  const count = pending.data?.items.length ?? 0
  if (!canWrite || count === 0) return null
  const label = count >= QUEUE_LIMIT ? `${QUEUE_LIMIT}+` : String(count)
  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>
          <h2>{count === 1 ? '1 proposta da rivedere' : `${label} proposte da rivedere`}</h2>
        </CardTitle>
        <CardDescription>
          Un assistente ha letto {count === 1 ? 'un contratto o una giornata' : 'contratti e giornate'} dai
          tuoi documenti. Non succede niente finché non li confermi o li rifiuti.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button asChild variant="outline">
          <Link to="/app/proposals" search={{ stato: 'in_attesa' }}>
            Rivedi le proposte
            <ChevronRight className="ml-1 size-4" aria-hidden />
          </Link>
        </Button>
      </CardContent>
    </Card>
  )
}
