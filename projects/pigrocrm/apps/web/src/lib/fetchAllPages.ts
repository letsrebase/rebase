/** A cursor-paginated response: the shape every list endpoint answers with. */
interface CursorPage<T> {
  items: T[]
  next_cursor?: string | null
}

export interface AllPages<T> {
  items: T[]
  /** True only when `maxPages` ran out while the server still reported more
   *  (`next_cursor` still set): the caller got a partial list and can say so. */
  truncated: boolean
}

/**
 * A hard stop against a runaway walk, not a bound the product expects to reach: 100 pages
 * of the endpoints' 200-row maximum is 20,000 rows, far past what a solo or small-team
 * space holds. Crossing it is reported through `truncated` instead of looping on.
 */
export const DEFAULT_MAX_PAGES = 100

/**
 * Walks a cursor-paginated endpoint to its end and returns every row, in order.
 *
 * `fetchPage` receives the cursor of the page to read (`undefined` for the first) and
 * resolves to that page. One implementation of the loop, so every caller shares the same
 * page cap and the same way of saying the list was cut short.
 */
export async function fetchAllPages<T>(
  fetchPage: (cursor: string | undefined) => Promise<CursorPage<T>>,
  { maxPages = DEFAULT_MAX_PAGES }: { maxPages?: number } = {},
): Promise<AllPages<T>> {
  const items: T[] = []
  let cursor: string | undefined
  for (let page = 0; page < maxPages; page += 1) {
    const data = await fetchPage(cursor)
    items.push(...data.items)
    if (!data.next_cursor) return { items, truncated: false }
    cursor = data.next_cursor
  }
  return { items, truncated: true }
}
