import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useAuth } from '@/lib/auth'
import { tenantPrefix } from '@/lib/tenant'

export type DriveHealth = components['schemas']['DriveHealth']
export type GoogleDriveAccountRead = components['schemas']['GoogleDriveAccountRead']
export type DriveRootsUpdate = components['schemas']['DriveRootsUpdate']

/**
 * Where a Drive consent flow starts: a plain navigation, never a fetch, because it leaves
 * for Google. Named here because two places offer it, the settings panel and the
 * «Riprova» under a consent that came back with no session (REB-446).
 *
 * Under a space, and under the root's own name, the API answers at `/<slug>/api/...` and
 * the session cookie is scoped to that prefix: a plain anchor to `/api/...` reaches the
 * root API with no cookie and answers «Autenticazione richiesta» (live, 2026-09-09).
 */
export const DRIVE_OAUTH_START = `${tenantPrefix}/api/drive/oauth/start`

/**
 * Keyed the same way `gmailKeys` is, and for the same reason: one root under which a
 * mutation can invalidate everything Drive-related without knowing in advance what else
 * reads it. `health` is the bare prefix a mutation invalidates by (React Query's
 * default `exact: false` match), not what the query itself reads -- see
 * `healthForRole`.
 */
export const driveKeys = {
  health: ['drive', 'health'] as const,
  /**
   * What `useDriveHealth` actually reads, keyed by the viewer's own role (REB-562 fix
   * round 2, Greptile). `space_storage` names another admin and is computed only for
   * an admin actor, so a bare `health` key would keep answering from whichever role
   * first fetched it: a demotion made from another tab or session reaches `useIsAdmin`
   * within thirty seconds (`AuthProvider`'s own `me` poll, `lib/auth.tsx`), but nothing
   * would have told *this* query to forget what it already cached. Rolling the role
   * into the key makes a role change a genuinely different query -- nothing cached
   * under the new key -- so React Query refetches the moment `useAuth`'s `user.ruolo`
   * changes, rather than going on serving the pre-demotion response until some other
   * mutation happened to invalidate the bare key.
   */
  healthForRole: (ruolo: string | undefined) => [...driveKeys.health, ruolo] as const,
}

/**
 * `GET /api/drive/account` answers 200 even on an installation with no Google client
 * (see `routers/drive.py`'s own docstring), so this query is in `isError` only when the
 * request genuinely failed. "Drive non è configurato" arrives as data --
 * `configured: false` -- and is a screen, not an error.
 */
export function useDriveHealth() {
  const { user } = useAuth()
  return useQuery({
    queryKey: driveKeys.healthForRole(user?.ruolo),
    queryFn: () => unwrap(api.GET('/api/drive/account')),
  })
}

/**
 * No `elimina_messaggi` flag on the wire: Drive stores no correspondence of its own, so
 * there is nothing here for one to name -- unlike Gmail's disconnect.
 */
export function useDisconnectDrive() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      await unwrap(api.DELETE('/api/drive/account'))
    },
    // The bare prefix, not `healthForRole`: a disconnect does not know, and must not
    // need to know, which role's query is currently live.
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: driveKeys.health }),
  })
}

export function useSetDriveRoots() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: DriveRootsUpdate) =>
      unwrap(api.PATCH('/api/drive/account/roots', { body: payload })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: driveKeys.health }),
  })
}
