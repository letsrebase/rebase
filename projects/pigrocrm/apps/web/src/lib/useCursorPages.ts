import { keepPreviousData, useInfiniteQuery, type QueryKey } from '@tanstack/react-query'
import { useMemo } from 'react'

interface CursorPage<T> {
  items: T[]
  next_cursor?: string | null
}

/**
 * A cursor-paginated list read one page at a time, for the screen that is the list
 * itself: the first page on arrival, one more per `loadMore()`.
 *
 * The filters belong in `queryKey`, so a changed filter starts a fresh walk at the first
 * page and never resumes a cursor issued for another query. `keepPrevious` keeps the old
 * rows on screen until the new first page lands -- for a list filtered by typing, where
 * dropping back to the loading state on every settled keystroke is a flash.
 */
export function useCursorPages<T>({
  queryKey,
  fetchPage,
  keepPrevious = false,
}: {
  queryKey: QueryKey
  fetchPage: (cursor: string | undefined) => Promise<CursorPage<T>>
  keepPrevious?: boolean
}) {
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    placeholderData: keepPrevious ? keepPreviousData : undefined,
  })
  // Memoised on the page array's reference: a fresh `items` on every render would
  // rebuild the table's row model for renders the fetched pages had no part in.
  const items = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data?.pages],
  )
  return {
    items,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    hasMore: query.hasNextPage,
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
  }
}
