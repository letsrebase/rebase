import { CashSchedule } from '@/features/cash/schedule/CashSchedule'

/**
 * The tab «Scadenziario» (tab id `receivables`, kept for the bookmarks that name it): the cash
 * schedule's body under the Home's own header and tabs. It is the same component «Cassa» mounts,
 * so the two doors cannot drift apart (brief, «One schedule, two doors»). It has no period: what
 * is owed is owed today, and the horizon is the schedule's own select.
 */
export function ReceivablesTab() {
  return <CashSchedule />
}
