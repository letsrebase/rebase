import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'

/** A failed read: what happened and «Riprova», with nothing under it (brief, «Error»). */
export function Failure({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="space-y-3">
      <QueryErrorBanner error={error} />
      <Button variant="outline" size="sm" onClick={onRetry}>
        Riprova
      </Button>
    </div>
  )
}
