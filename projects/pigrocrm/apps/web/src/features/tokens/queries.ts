import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useAuth } from '@/lib/auth'
import { queryKeys } from '@/lib/query'

export type TokenRecord = components['schemas']['PatRead']
export type CreatedToken = components['schemas']['CreatedToken']
export type TeamTokenRecord = components['schemas']['TeamPatRead']

/**
 * Scoped to the caller's own tokens server-side (`PatService.list` filters on
 * `actor.id`), not by role -- any authenticated user, any role, can list,
 * create and revoke their own tokens. That is why this screen lives at its
 * own route (`/app/token`) rather than inside the admin-only Impostazioni
 * section: a personal access token is how a human connects an agent to this
 * CRM at all, so gating it behind "admin" would mean a collaborator could
 * never use the product's own premise.
 *
 * `enabled`/the `?? ''` fallback below matter together, not separately: this
 * hook is only ever mounted under `/app/token`, itself only reachable once
 * `routes/app.tsx`'s own guard has already resolved a real, logged-in `user`
 * -- so `userId` is never actually empty in production -- but asserting that
 * instead of guarding it would make a future refactor's mistake a live,
 * empty-id request against `GET /api/tokens` rather than a query that simply
 * never fires.
 */
export function useTokens() {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  return useQuery({
    queryKey: queryKeys.tokens(userId),
    queryFn: () => unwrap(api.GET('/api/tokens')),
    enabled: Boolean(userId),
  })
}

/**
 * The response carries the one and only copy of the plaintext token
 * (`CreatedToken.token` -- tokens.py's own docstring: "shown once, at
 * creation, and never again"; the server stores only a SHA-256 hash).
 * `TokensPanel`'s `issued` dialog is the one place it is held, and only for
 * as long as that dialog is open.
 */
export function useCreateToken() {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const userId = user?.id ?? ''
  return useMutation({
    mutationFn: (input: { nome: string; full_access: boolean }) =>
      unwrap(api.POST('/api/tokens', { body: input })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.tokens(userId) }),
  })
}

/**
 * Whether the space lets a token be given the invoice and period-lock tools at all (its
 * `mcp_full_access`, the ceiling). Its own read rather than the space settings, which are
 * an admin's: a collaboratore who makes a token has to be told why the box is off.
 */
export function useTokenOptions() {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  return useQuery({
    queryKey: queryKeys.tokenOptions(userId),
    queryFn: () => unwrap(api.GET('/api/tokens/options')),
    enabled: Boolean(userId),
  })
}

/** The other members' live tokens, for an administrator only: the server refuses anybody
 *  else, so the panel asks only when `enabled`. */
export function useTeamTokens(enabled: boolean) {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  return useQuery({
    queryKey: queryKeys.teamTokens(userId),
    queryFn: () => unwrap(api.GET('/api/tokens/team')),
    enabled: enabled && Boolean(userId),
  })
}

/** Opens or closes one token's invoice and period-lock tools. The owner or an admin; the
 *  server refuses an agent credential, so a token cannot open itself. */
export function useSetTokenFullAccess() {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const userId = user?.id ?? ''
  return useMutation({
    mutationFn: (input: { tokenId: string; full_access: boolean }) =>
      unwrap(
        api.PATCH('/api/tokens/{token_id}', {
          params: { path: { token_id: input.tokenId } },
          body: { full_access: input.full_access },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.tokens(userId) }),
  })
}

/** Irreversible -- `PatService` has no "un-revoke" -- so `TokensPanel` guards
 *  this behind a confirmation, the same as an irreversible pipeline-stage
 *  delete. */
export function useRevokeToken() {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const userId = user?.id ?? ''
  return useMutation({
    mutationFn: (tokenId: string) =>
      unwrap(api.DELETE('/api/tokens/{token_id}', { params: { path: { token_id: tokenId } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.tokens(userId) }),
  })
}
