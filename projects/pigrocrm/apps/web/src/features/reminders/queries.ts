import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import { queryKeys } from '@/lib/query'
import { isOpen, type Reminder, type Severity, type SnoozeOption } from './fields'

/**
 * How often the list is read again while a window is open. The API recomputes the whole
 * list from the data on every call (spec 2026-10-26 §8, REB-733), so the badge in the
 * sidebar, which is on every page, polls no faster than a minute; a window coming back to
 * focus reads it once more, and a snooze or an acknowledge reads it at once.
 */
export const REFETCH_MS = 60_000

/**
 * One definition for the page and the sidebar's badge, and so one cache entry: what the
 * page changes is what the badge counts, with no second request to keep in step.
 */
const remindersOptions = queryOptions({
  queryKey: queryKeys.reminders,
  queryFn: () => unwrap(api.GET('/api/reminders')),
  refetchInterval: REFETCH_MS,
  refetchOnWindowFocus: true,
})

/** The caller's reminders, severest first, with what they did about each one. */
export function useReminders() {
  return useQuery(remindersOptions)
}

/**
 * The unread count of the sidebar: the reminders still `open` for the caller. Snoozed and
 * acknowledged ones do not count, and neither does a list that failed to load: a badge
 * that cannot be trusted is no badge, so the count is `undefined` and the entry draws none.
 */
export function useUnreadCount(): number | undefined {
  return useQuery({
    ...remindersOptions,
    select: (reminders: Reminder[]) => reminders.filter(isOpen).length,
  }).data
}

/**
 * What both writes do afterwards. The row is changed in the cache from the API's answer so
 * the page and the count move in the same frame, and the list is read again to settle on
 * what the server computes (a reminder that climbed in the meantime is back, by its rule).
 */
function useSettled() {
  const queryClient = useQueryClient()
  return (state: {
    key: string
    acknowledged_severity: Severity | null
    snoozed_until: string | null
  }) => {
    queryClient.setQueryData<Reminder[]>(queryKeys.reminders, (list) =>
      list?.map((reminder) =>
        reminder.key !== state.key
          ? reminder
          : state.snoozed_until
            ? { ...reminder, status: 'snoozed', snoozed_until: state.snoozed_until }
            : { ...reminder, status: 'acknowledged', snoozed_until: null },
      ),
    )
    void queryClient.invalidateQueries({ queryKey: queryKeys.reminders })
  }
}

/** «Visto»: marks the reminder seen at the severity it has on screen. */
export function useAcknowledge() {
  const settled = useSettled()
  return useMutation({
    mutationFn: (reminder: Pick<Reminder, 'key' | 'severity'>) =>
      unwrap(
        api.POST('/api/reminders/acknowledge', {
          body: { key: reminder.key, severity: reminder.severity },
        }),
      ),
    onSuccess: (state) => settled({ ...state, snoozed_until: null }),
  })
}

/** Postpones the reminder to one of the four days the API offers. */
export function useSnooze() {
  const settled = useSettled()
  return useMutation({
    mutationFn: ({
      reminder,
      until,
    }: {
      reminder: Pick<Reminder, 'key' | 'severity'>
      until: SnoozeOption
    }) =>
      unwrap(
        api.POST('/api/reminders/snooze', {
          body: { key: reminder.key, severity: reminder.severity, until },
        }),
      ),
    onSuccess: (state) => settled(state),
  })
}
