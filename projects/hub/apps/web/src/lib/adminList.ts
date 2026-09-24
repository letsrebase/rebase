import { useEffect, useState } from 'react'

/** Every admin list's search-and-filter shape (REB-286, REB-413): a debounce for the
 *  search box, and the two-way distinction between "the table itself has nothing in
 *  it" and "a filter narrowed it to nothing" -- kept out of `pages/admin/lists.tsx` so
 *  it, `useDebounce` and `isFilterActive` stay component-only files under the
 *  `react-refresh/only-export-components` rule. */
export const SEARCH_DEBOUNCE_MS = 300

/** Debounces a fast-changing value so a keystroke does not trigger a request until the
 *  admin stops typing for `delayMs`: the search boxes on Talenti, Aziende and Match
 *  all use this at `SEARCH_DEBOUNCE_MS`. */
export function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

/** «Nessun risultato per questi filtri» when a search or a filter narrowed an
 *  otherwise non-empty table down to nothing, the plain sentence when the table itself
 *  has nothing in it yet: a zero from a filter and a zero from an empty table are
 *  different facts, and only one of them goes away by clearing something. */
export function isFilterActive(filters: Record<string, unknown>): boolean {
  return Object.values(filters).some((value) => value !== undefined && value !== '')
}
