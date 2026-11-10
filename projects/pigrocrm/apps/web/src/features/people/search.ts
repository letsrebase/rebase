/**
 * The shape `GET /api/people`'s `customer_id` accepts
 * (`apps/api/src/pigrocrm_api/routers/people.py` types it `UUID | None`), checked here
 * before the value ever reaches a request. A stale bookmark, a typo or a hand-edited
 * query string carrying anything else must be dropped exactly like an empty `search`
 * is dropped below -- not sent to the API, where it would 422 and, through `unwrap()`,
 * turn the whole list into `DataTable`'s error banner over one bad URL parameter.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** The route's `validateSearch`: an empty or non-string value is dropped rather than
 *  carried as `?search=`/`?customer_id=`, so the URL never claims a filter that is not
 *  applied. */
export function parsePeopleSearch(search: Record<string, unknown>): {
  search?: string
  customer_id?: string
} {
  return {
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
    customer_id:
      typeof search.customer_id === 'string' && UUID_RE.test(search.customer_id)
        ? search.customer_id
        : undefined,
  }
}
