import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useLegalEntity } from '@/lib/legalEntity'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type SearchResults = components['schemas']['SearchResults']
export type SearchGroup = components['schemas']['SearchGroup']
export type SearchHit = components['schemas']['SearchHit']
export type SearchEntity = SearchGroup['entity']

/**
 * Below this the palette issues no request at all. Spec §8.3: a trigram index cannot
 * serve a pattern from which no trigram can be extracted, and a two-character term on
 * 50 000 customers returns thousands of rows, which is not an answer either. The API
 * enforces the same bound (`SearchQuery.termine` has `min_length=3`, and the router
 * repeats it so a direct HTTP client gets a 422); this is the half that stops the
 * request being made.
 */
export const MIN_TERM_LENGTH = 3

/**
 * Residuo B2 gets worse here than on the deal list: the palette queries four entities on
 * every keystroke. 250 ms is short enough to feel immediate and long enough that typing
 * "rossi" is one request, not three.
 */
export const SEARCH_DEBOUNCE_MS = 250

function useDebounced(value: string, delayMs: number): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return settled
}

/**
 * The previous request is not aborted by hand. TanStack Query keys the cache by the term,
 * so an in-flight response for "ross" resolves into its own entry and cannot paint over
 * the rendering of "rossi"; the component reads only the entry for the term it is
 * currently showing. Manual `AbortController` plumbing would add a second mechanism for
 * the property the key already gives.
 *
 * `enabled` is the guard, not an early `return`: a hook must never be called with an
 * argument it cannot serve (residuo B1), and a short term is exactly that case.
 *
 * `debounced` is returned alongside the query state because every one of §8.6's states is
 * a statement about the term that was actually *asked*, not the one being typed: naming
 * the live term in "Nessun risultato per «…»" would report a result under a term no
 * request has been made for yet.
 */
export function useGlobalSearch(term: string) {
  const debounced = useDebounced(term.trim(), SEARCH_DEBOUNCE_MS)
  // The sidebar's azienda narrows the palette too (REB-625): under azienda A, «brief»
  // finds A's documents and A's customers' people, and nothing of B's.
  const { selected } = useLegalEntity()
  const query = useQuery({
    queryKey: queryKeys.search(debounced, selected),
    queryFn: () =>
      unwrap(
        api.GET('/api/search', {
          params: { query: { q: debounced, azienda_id: selected ?? undefined } },
        }),
      ),
    enabled: debounced.length >= MIN_TERM_LENGTH,
    // A palette is re-opened constantly and the same term is retyped constantly. Ten
    // seconds is long enough to make reopening instant and short enough that a record
    // created a moment ago is findable.
    staleTime: 10_000,
  })
  return { ...query, debounced }
}

export const ENTITY_LABELS: Record<SearchEntity, string> = {
  customer: 'Clienti',
  person: 'Persone',
  deal: 'Deal',
  document: 'Documenti',
  invoice: 'Fatture',
}
