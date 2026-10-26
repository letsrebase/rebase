import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import { queryKeys } from '@/lib/query'
import type { PreferenceWrite, Scope } from './preferences'

/** Both layers of settings and what they resolve to for the caller: one read for the
 *  space's editor and the personal one alike. */
export function useReminderPreferences() {
  return useQuery({
    queryKey: queryKeys.reminderPreferences,
    queryFn: () => unwrap(api.GET('/api/reminders/preferences')),
  })
}

/**
 * What a write changes is what the next pass raises, so the Avvisi list is read again too:
 * a threshold or a switch moves what the page and the sidebar count show. The mutation
 * stays pending until the settings are read back, so a control never shows a value the
 * server has not confirmed.
 */
function useRefreshAfterWrite() {
  const queryClient = useQueryClient()
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.reminderPreferences }),
      queryClient.invalidateQueries({ queryKey: queryKeys.reminders }),
    ])
}

/**
 * Sets the fields given on one layer's row for a type, or for the settings of no type
 * (`type: 'general'`). A field sent as `null` goes back to inheriting; a field left out is
 * not touched (`PATCH` semantics of the API).
 */
export function useWritePreference(scope: Scope) {
  const refresh = useRefreshAfterWrite()
  return useMutation({
    mutationFn: ({ type, body }: { type: string; body: PreferenceWrite }) =>
      unwrap(
        api.PATCH('/api/reminders/preferences/{scope}/{type_key}', {
          params: { path: { scope, type_key: type } },
          body,
        }),
      ),
    onSuccess: refresh,
  })
}

/**
 * The way back: deletes the layer's row for the type, so a later change of the default
 * still reaches a person who goes back to it. Never a write of the default's value.
 */
export function useResetPreference(scope: Scope) {
  const refresh = useRefreshAfterWrite()
  return useMutation({
    mutationFn: (type: string) =>
      unwrap(
        api.DELETE('/api/reminders/preferences/{scope}/{type_key}', {
          params: { path: { scope, type_key: type } },
        }),
      ),
    onSuccess: refresh,
  })
}
