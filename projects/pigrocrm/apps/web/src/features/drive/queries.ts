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
 * `healthForViewer`.
 */
export const driveKeys = {
  health: ['drive', 'health'] as const,
  /**
   * What `useDriveHealth` actually reads, keyed by the viewer's own id *and* role
   * (REB-562 fix rounds 2 and 4, Greptile then CodeRabbit). `space_storage` names
   * another admin and `account` is the viewer's own connected Drive, both computed
   * only for -- and about -- one specific person, so a bare `health` key would keep
   * answering from whichever person first fetched it.
   *
   * The id is what a role alone cannot cover: two admins share `ruolo`, so a session
   * switch from admin A to admin B in the same tab, without a `logout()` in between --
   * a token client that swaps a cookie, or a browser profile shared between two
   * people -- would key both fetches identically on role and hand B the cache A left
   * behind, complete with A's own email address. The role stays in the key too,
   * alongside the id, for round 2's own reason: a demotion changes `ruolo` while
   * `user.id` stays the same person, and `space_storage` must disappear at that
   * moment as well, not only when the person themselves changes.
   */
  healthForViewer: (userId: string | undefined, ruolo: string | undefined) =>
    [...driveKeys.health, userId, ruolo] as const,
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
    queryKey: driveKeys.healthForViewer(user?.id, user?.ruolo),
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
    // The bare prefix, not `healthForViewer`: a disconnect does not know, and must not
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
