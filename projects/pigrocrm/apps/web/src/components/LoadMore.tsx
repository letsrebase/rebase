import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'

/**
 * The affordance under a list that has more pages than it shows: a button that reads one
 * page further, and a sentence saying that the list is not complete rather than leaving
 * the user to notice. `message` is the caller's whole sentence ("Mostrati 50 clienti, ce
 * ne sono altri.") and so is the button's `label` ("Carica altri"), because the noun's
 * gender is the caller's to agree.
 *
 * `role="status"` on the sentence, so a screen reader announces the appended count after a
 * click and this control's own disappearance once the list is complete. `hasError` shows
 * the product's usual `QueryErrorBanner`: the rows already on screen are unaffected by a
 * failed next page, and `DataTable` stays silent while it has rows to show, so without it
 * a failed click would read as a silent no-op.
 */
export function LoadMore({
  label,
  message,
  isFetchingMore,
  hasError,
  error,
  onLoadMore,
}: {
  label: string
  message: string
  isFetchingMore: boolean
  hasError: boolean
  error: unknown
  onLoadMore: () => void
}) {
  return (
    <div className="mt-4 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={onLoadMore} disabled={isFetchingMore}>
          {isFetchingMore ? 'Caricamento…' : label}
        </Button>
        <span role="status" className="text-sm text-muted-foreground">
          {message}
        </span>
      </div>
      {hasError && <QueryErrorBanner error={error} />}
    </div>
  )
}
