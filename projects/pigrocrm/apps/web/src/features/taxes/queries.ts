import { useQuery } from '@tanstack/react-query'
import { useLegalEntity } from '@/lib/legalEntity'
import { api, unwrap } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { queryKeys } from '@/lib/query'
import { toIsoDate } from '@/lib/dates'
import { nextDays, type DueDay } from './dates'

/** How many dates the Home carries (brief: at most two rows). */
export const HOME_DATES = 2

/**
 * `GET /api/analytics/fiscal/calendar`: the dates of calendar `year` for one legal entity, from
 * its pack. One legal entity's, like the estimate: the one `legalEntityId` names, or the
 * sidebar's when the caller names none; asked without a legal entity only once the list of legal
 * entities has answered, since on a space with two the server refuses a read that names none.
 * `enabled` is the caller's role and module gate: a role that may not read it makes no request.
 */
export function useTaxCalendar(year: number, legalEntityId?: string | null, enabled = true) {
  const { selected, loaded } = useLegalEntity()
  const legal_entity = legalEntityId === undefined ? selected : legalEntityId
  const query = legal_entity === null ? { year } : { year, legal_entity_id: legal_entity }
  return useQuery({
    queryKey: queryKeys.taxCalendar(year, legal_entity),
    queryFn: () => unwrap(api.GET('/api/analytics/fiscal/calendar', { params: { query } })),
    enabled: enabled && (legal_entity !== null || loaded),
  })
}

/**
 * The next dates of one legal entity for the Home: the first `HOME_DATES` days on or after today,
 * read from this calendar year and, when fewer are left, from the next (the December advance is
 * followed by January's payments). `hasCalendar` says whether the pack carries dates at all, so
 * a pack with none is told from a quiet stretch; the Home draws nothing for the first.
 */
export function useNextTaxDates(legalEntityId?: string | null, enabled = true) {
  const allowed = useCan('get_tax_calendar') && enabled
  const now = new Date()
  const today = toIsoDate(now)
  const year = now.getFullYear()
  const current = useTaxCalendar(year, legalEntityId, allowed)
  const ahead = current.data ? nextDays(current.data.dates, today, HOME_DATES) : []
  const needMore = current.isSuccess && ahead.length < HOME_DATES
  const following = useTaxCalendar(year + 1, legalEntityId, allowed && needMore)
  const days: DueDay[] = needMore
    ? [...ahead, ...nextDays(following.data?.dates ?? [], today, HOME_DATES - ahead.length)]
    : ahead
  const waiting = current.isPending || (needMore && following.isPending)
  return {
    today,
    days,
    waiting,
    error: current.error ?? (needMore ? following.error : null),
    hasCalendar: (current.data?.dates.length ?? 0) + (following.data?.dates.length ?? 0) > 0,
    refetch: () => {
      void current.refetch()
      if (needMore) void following.refetch()
    },
  }
}
