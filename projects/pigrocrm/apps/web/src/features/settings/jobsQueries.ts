import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type ScheduledJob = components['schemas']['ScheduledJobRead']
export type JobState = ScheduledJob['state']

/**
 * How often the page reads again while it is open: the slowest job is the fifteen-minute
 * one, so a minute is as fresh as anything on it can be, and a window coming back to focus
 * reads once more.
 */
const REFETCH_MS = 60_000

/** What the cron commands last did in this space (REB-764). An admin's read. */
export function useScheduledJobs() {
  return useQuery({
    queryKey: queryKeys.scheduledJobs,
    queryFn: () => unwrap(api.GET('/api/scheduled-jobs')),
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: true,
  })
}
