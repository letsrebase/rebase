import type { Task } from '@/features/calendar/queries'
import { toIsoDate } from '@/lib/dates'
import { LINK_COLUMN, type LinkValue } from './links'
import type { TaskFilters } from './queries'

export type Status = Task['status']
export type DueDate = 'overdue' | 'week' | 'without'

/** The assignee select's own words for «no filter» and «me»: neither is a user id. */
export const EVERYONE = 'all'
export const ME = 'me'

/** `days` from today, in the browser's calendar, as the API's `AAAA-MM-GG`. The server's
 *  own «oggi» is the emitter's zone; the two agree except in the hours around midnight
 *  for somebody working in another zone, which is the same trade the calendar's month
 *  default makes. */
function daysFromToday(days: number): string {
  const day = new Date()
  day.setDate(day.getDate() + days)
  return toIsoDate(day)
}

/** What the filter row says, as the query the API takes. Pure, so a test reads it. */
export function toFilters(state: {
  status: Status | null
  due_date: DueDate | null
  assignee: string
  meId: string | null
  link: LinkValue | null
  archived: boolean
}): TaskFilters {
  const filters: TaskFilters = { sort: 'due_date', dir: 'asc', limit: 50 }
  if (state.status) filters.status = state.status
  if (state.archived) filters.archived = true
  if (state.assignee === ME) {
    if (state.meId) {
      filters.assigned_to = state.meId
      filters.include_unassigned = true
    }
  } else if (state.assignee !== EVERYONE) {
    filters.assigned_to = state.assignee
  }
  // «Scadute» is «due before today»: `due_within` is inclusive, so yesterday. The undated
  // are left out by the API itself, which is the point of the filter.
  // Overdue is an open commitment past its date (spec §3.2): a completed one is not late,
  // so the chip implies `status=open` whatever the state chips say.
  if (state.due_date === 'overdue') {
    filters.due_within = daysFromToday(-1)
    filters.status = 'open'
  }
  if (state.due_date === 'week') filters.due_within = daysFromToday(7)
  if (state.due_date === 'without') filters.without_due_date = true
  if (state.link) Object.assign(filters, { [LINK_COLUMN[state.link.kind]]: state.link.id })
  return filters
}
