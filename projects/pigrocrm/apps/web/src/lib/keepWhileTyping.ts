import type { QueryKey } from '@tanstack/react-query'

function withoutSearch(key: QueryKey): string {
  return JSON.stringify(
    key.map((part) => {
      if (typeof part !== 'object' || part === null || Array.isArray(part)) return part
      return Object.entries(part).filter(([name]) => name !== 'search')
    }),
  )
}

/**
 * A `placeholderData` for a list filtered by typing: the previous rows stay up only when
 * the typed `search` is the one thing that changed between the previous key and this one.
 * A chip, the sidebar's legal entity or a customer filter changing makes the previous rows
 * another scope's, so those get the normal loading state instead.
 */
export function keepWhileTyping<T>(currentKey: QueryKey) {
  const current = withoutSearch(currentKey)
  return (previousData: T | undefined, previousQuery: { queryKey: QueryKey } | undefined): T | undefined =>
    previousQuery !== undefined && withoutSearch(previousQuery.queryKey) === current
      ? previousData
      : undefined
}
