import { useInfiniteQuery, type QueryKey } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { ApiError } from './api'

/** Everything the admin lists share (REB-286, REB-413) that is not a component: the
 *  debounce of the search box, the cursor-paged query, the «no filter» value of a
 *  `Select`, and the two-way distinction between "the table itself has nothing in it"
 *  and "a filter narrowed it to nothing". Kept out of `pages/admin/list-kit.tsx` so
 *  that file exports components only, under the `react-refresh/only-export-components`
 *  rule. */
export const SEARCH_DEBOUNCE_MS = 300

/** Debounces a fast-changing value so a keystroke does not trigger a request until the
 *  admin stops typing for `delayMs`: every admin search box uses this at
 *  `SEARCH_DEBOUNCE_MS`. */
export function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

/** A page the API answers with the cursor of the next one, `null` on the last. */
interface CursorPage {
  next_cursor?: string | null
}

/** The `items` of a page that calls them so: what most lists pass as `itemsOf`. */
export function pageItems<T>(page: { items: T[] }): T[] {
  return page.items
}

/** A list read a page at a time by cursor (REB-286): the infinite query every admin
 *  list repeated, with the pages flattened into `items`. `itemsOf` must be a stable
 *  function (module-level), or the flattened list is rebuilt on every render. */
export function useCursorList<Page extends CursorPage, Item>({
  queryKey,
  fetchPage,
  itemsOf,
}: {
  queryKey: QueryKey
  fetchPage: (cursor: string | undefined) => Promise<Page>
  itemsOf: (page: Page) => Item[]
}) {
  const list = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const pages = list.data?.pages
  const items = useMemo(() => pages?.flatMap(itemsOf) ?? [], [pages, itemsOf])
  return { list, items }
}

/** «No filter» for a `Select`, which needs a non-empty string: the query string simply
 *  omits the key instead, same reasoning as `ANY` in the CRM's own fatture list. */
export const ANY = 'tutti'

export function boolToSelect(value: boolean | undefined): string {
  return value === undefined ? ANY : value ? 'si' : 'no'
}

export function selectToBool(value: string): boolean | undefined {
  return value === 'si' ? true : value === 'no' ? false : undefined
}

/** «Nessun risultato per questi filtri» when a search or a filter narrowed an
 *  otherwise non-empty table down to nothing, the plain sentence when the table itself
 *  has nothing in it yet: a zero from a filter and a zero from an empty table are
 *  different facts, and only one of them goes away by clearing something. */
export function isFilterActive(filters: object): boolean {
  return Object.values(filters).some((value: unknown) => value !== undefined && value !== '')
}

/** What a failed write says on the page: the API's own sentence when it gave one, the
 *  caller's fallback when the request never got an answer, `null` while nothing failed. */
export function failureMessage(error: unknown, fallback: string): string | null {
  if (error === null || error === undefined) return null
  return error instanceof ApiError ? error.message : fallback
}
