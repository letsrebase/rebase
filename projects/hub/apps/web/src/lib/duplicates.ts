import { useQuery } from '@tanstack/react-query'
import { admin, type DuplicateList, type MergeResult } from './api'

/** The one cache entry every duplicate mark reads and the merge invalidates (REB-800). */
export const DUPLICATES_KEY = ['talenti-duplicati'] as const

/** `GET /api/hub/talent/duplicati`. A failed read leaves the marks off: they are a hint
 *  beside a list that works without them, never a reason to break it. */
export function useDuplicates() {
  return useQuery({ queryKey: DUPLICATES_KEY, queryFn: () => admin.duplicates() })
}

/** How many pairs an admin has to look at, the two groups together. */
export function pairCount(list: DuplicateList): number {
  return list.unibili.length + list.da_rivedere.length
}

function rows(count: number): string {
  return count === 1 ? '1 riga spostata' : `${count} righe spostate`
}

/** The line shown once a merge went through: what moved, and the one thing left to do. */
export function mergeResultLine(result: MergeResult): string {
  const moved = Object.values(result.moved).reduce((total, count) => total + count, 0)
  const stayed = Object.values(result.lasciati).reduce((total, count) => total + count, 0)
  const parts = [`Unite: ${rows(moved)}.`]
  if (stayed > 0) {
    parts.push(
      stayed === 1
        ? '1 riga è rimasta dov’era perché la scheda tenuta ne aveva già una uguale.'
        : `${stayed} righe sono rimaste dov’erano perché la scheda tenuta ne aveva già una uguale.`,
    )
  }
  if (result.scheda_da_rigenerare) parts.push('La scheda anonima va riscritta.')
  return parts.join(' ')
}
