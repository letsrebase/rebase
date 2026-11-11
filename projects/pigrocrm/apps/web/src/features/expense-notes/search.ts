import type { Bucket, NoteStatus } from './queries'

/**
 * `?tab=&status=&person=&category=&legal_entity=&note=`: the administrator's tab, a staff member's
 * status filter, the three filters of the administrator and the open note are in the URL, so a
 * link reopens the same reading. Nothing here says who is looking: the page reads the tab for an
 * administrator and the status for a staff member, and ignores the other.
 */
export type ExpenseNotesSearch = {
  tab?: Bucket
  status?: NoteStatus
  person?: string
  category?: string
  legal_entity?: string
  note?: string
}

export const BUCKETS: readonly Bucket[] = ['to_approve', 'to_reimburse', 'closed']
export const STATUSES: readonly NoteStatus[] = ['submitted', 'approved', 'rejected', 'reimbursed']

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value !== '' ? value : undefined

export function validateExpenseNotesSearch(search: Record<string, unknown>): ExpenseNotesSearch {
  return {
    tab: BUCKETS.find((bucket) => bucket === search.tab),
    status: STATUSES.find((status) => status === search.status),
    person: text(search.person),
    category: text(search.category),
    legal_entity: text(search.legal_entity),
    note: text(search.note),
  }
}

/** The search without the keys that say nothing, so the URL carries only what applies. */
export function compactSearch(search: ExpenseNotesSearch): ExpenseNotesSearch {
  return Object.fromEntries(
    Object.entries(search).filter(([, value]) => value !== undefined && value !== ''),
  ) as ExpenseNotesSearch
}
