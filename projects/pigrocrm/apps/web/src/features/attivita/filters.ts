import type { Attivita } from '@/features/calendar/queries'
import { toIsoDate } from '@/lib/dates'
import { LINK_COLUMN, type LinkValue } from './links'
import type { AttivitaFilters } from './queries'

export type Stato = Attivita['stato']
export type Scadenza = 'scadute' | 'settimana' | 'senza'

/** The assignee select's own words for «no filter» and «me»: neither is a user id. */
export const EVERYONE = 'tutte'
export const ME = 'io'

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
  stato: Stato | null
  scadenza: Scadenza | null
  assignee: string
  meId: string | null
  link: LinkValue | null
  archiviate: boolean
}): AttivitaFilters {
  const filters: AttivitaFilters = { sort: 'scadenza', dir: 'asc', limit: 50 }
  if (state.stato) filters.stato = state.stato
  if (state.archiviate) filters.archiviate = true
  if (state.assignee === ME) {
    if (state.meId) filters.assegnata_a = state.meId
  } else if (state.assignee !== EVERYONE) {
    filters.assegnata_a = state.assignee
  }
  // «Scadute» is «due before today»: `scade_entro` is inclusive, so yesterday. The undated
  // are left out by the API itself, which is the point of the filter.
  // Overdue is an open commitment past its date (spec §3.2): a completed one is not late,
  // so the chip implies `stato=aperta` whatever the state chips say.
  if (state.scadenza === 'scadute') {
    filters.scade_entro = daysFromToday(-1)
    filters.stato = 'aperta'
  }
  if (state.scadenza === 'settimana') filters.scade_entro = daysFromToday(7)
  if (state.scadenza === 'senza') filters.senza_scadenza = true
  if (state.link) Object.assign(filters, { [LINK_COLUMN[state.link.kind]]: state.link.id })
  return filters
}
