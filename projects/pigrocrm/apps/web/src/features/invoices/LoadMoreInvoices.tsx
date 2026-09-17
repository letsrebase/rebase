import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@/components/ui/button'

/**
 * The affordance both `InvoicesList` and `InvoicesTab` show under a truncated page: a
 * button that walks `next_cursor` one page further (REB-231), and a count so the fact
 * that the register is not fully shown is stated rather than left for the user to
 * notice on their own -- the same "say the real count" instinct `CommandPalette`'s
 * «vedi tutti» row and the Kanban's `TruncatedNotice` both follow, in this list's own
 * plain tone since a single extra page is routine here, not the pathological case
 * `TruncatedNotice` exists for.
 *
 * `role="status"` on the count, like every other line in this app that states a
 * list's own condition (the «scadute» notice right above this on the list page, the
 * Kanban's `TruncatedNotice`): a screen reader announces both the appended count after
 * a click and this control's own disappearance once the register is fully loaded,
 * neither of which a plain `<span>` would say.
 *
 * `hasError` shows the same `QueryErrorBanner` every other failed read in this product
 * uses: the rows already on screen are unaffected by a failed next page, and
 * `DataTable`'s own error banner stays silent on purpose while it has rows to show, so
 * without this a failed click here would read as a silent no-op.
 */
export function LoadMoreInvoices({
  shown,
  isFetchingMore,
  hasError,
  error,
  onLoadMore,
}: {
  shown: number
  isFetchingMore: boolean
  hasError: boolean
  error: unknown
  onLoadMore: () => void
}) {
  return (
    <div className="mt-4 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={onLoadMore} disabled={isFetchingMore}>
          {isFetchingMore ? 'Caricamento…' : 'Carica altre'}
        </Button>
        <span role="status" className="text-sm text-muted-foreground">
          Mostrate {shown} fatture, ce ne sono altre.
        </span>
      </div>
      {hasError && <QueryErrorBanner error={error} />}
    </div>
  )
}
