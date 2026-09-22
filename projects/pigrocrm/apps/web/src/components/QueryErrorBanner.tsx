import { toProblem } from '@/lib/api'

/**
 * The one rendering of "we could not ask", never to be confused with "there is
 * nothing to show" -- used by `DataTable` (its own `isError`/`error` props) and
 * directly by the Deal Kanban board, which has no table underneath it to fall
 * back on. `DataTable` already treats loading and a genuine empty result as
 * deliberately different shapes (see that file's own docstring: "a slow network
 * masquerading as there is nothing here" is a different claim it refuses to
 * make on the caller's behalf) -- a failed request is a third such claim, and
 * collapsing it into either of the other two is exactly the defect this
 * component exists to close. Without it, a failed `GET /api/deals` rendered
 * every Kanban column at 0 / 0,00 € with five "Nessun deal" and nothing else on
 * screen: indistinguishable from a tenant with genuinely no deals.
 *
 * Reuses the exact destructive-toned banner `DynamicForm`'s own unattributed-
 * error banner established (and this feature's own truncation notices already
 * copy), rather than inventing a fourth visual language for "something is
 * wrong" in this product. The message is the server's own, via `toProblem`,
 * never a client-side rewording -- the same discipline `Timeline.tsx`'s
 * identical-looking error line already follows.
 */
export function QueryErrorBanner({ error }: { error: unknown }) {
  return (
    <p
      role="alert"
      className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
    >
      {toProblem(error).detail}
    </p>
  )
}
