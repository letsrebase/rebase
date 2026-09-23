import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { tenantPrefix } from '@/lib/tenant'

export type GmailHealth = components['schemas']['GmailHealth']
export type GoogleAccountRead = components['schemas']['GoogleAccountRead']
export type GmailMessageRead = components['schemas']['GmailMessageRead']
export type SyncReport = components['schemas']['SyncReport']

export type GmailEntityType = 'customer' | 'person' | 'deal'

/** The scope without which nothing is read at all. Named here rather than in a
 *  component, because both the settings panel and the sync notice have to agree on
 *  what "the sync will run" means. */
export const GMAIL_READONLY_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'

/**
 * Where a consent flow starts: a plain navigation, never a fetch, because it leaves for
 * Google. Named here because two screens offer it, the settings panel and the start
 * page's Gmail door (REB-222).
 *
 * Under a space, and under the root's own name, the API answers at `/<slug>/api/...` and
 * the session cookie is scoped to that prefix: a plain anchor to `/api/...` reaches the
 * root API with no cookie and answers «Autenticazione richiesta» (live, 2026-09-09).
 */
export const GMAIL_OAUTH_START = `${tenantPrefix}/api/gmail/oauth/start`

/**
 * Keyed locally rather than in `lib/query.ts`'s central `queryKeys`, which is where
 * every other feature's keys live. The prefix matters more than the location here:
 * `['gmail', 'messages', ...]` lets a sync invalidate every entity's Email tab at once
 * without enumerating which customers happen to be cached, and that only works if the
 * two keys share a root nobody else writes under.
 */
export const gmailKeys = {
  health: ['gmail', 'health'] as const,
  messages: (entityType: string, entityId: string) =>
    ['gmail', 'messages', entityType, entityId] as const,
}

/**
 * `GET /api/gmail/account` answers 200 even on an installation with no Google client
 * (see the router's own docstring), so this query is in `isError` only when the request
 * genuinely failed. "Gmail non è configurato" arrives as data -- `configured: false` --
 * and is a screen, not an error.
 */
export function useGmailHealth() {
  return useQuery({
    queryKey: gmailKeys.health,
    queryFn: () => unwrap(api.GET('/api/gmail/account')),
  })
}

/**
 * Whether this installation has Gmail at all -- the `configured` half of `GmailHealth`,
 * which is a fact about the environment and not about whether anybody consented yet.
 *
 * It is what decides whether an entity page grows an Email tab. An installation with no
 * Google client will never file a message against a customer, so a tab there would open
 * onto a permanent "nessuna email" -- the same "a tab showing zeros is worse than no
 * tab" rule `EntityDetailLayout` already applies to Economia. A *configured* install
 * whose owner has not consented yet does get the tab: for them the emptiness is
 * temporary and the tab's own text says how to end it.
 *
 * Free to call from anywhere: it shares `gmailKeys.health` with the shell banner, which
 * is mounted on every authenticated page, so this is a cache read rather than a request
 * (`staleTime` is 30s -- see `lib/query.ts`). A failed or pending health read answers
 * `false`, which hides a tab for a moment rather than showing one that then errors.
 */
export function useGmailConfigured(): boolean {
  return useGmailHealth().data?.configured === true
}

/**
 * Whether another cycle is actually going to run: a mailbox that is connected, whose
 * credential is `active`, and which was granted the read scope. Exactly the condition
 * the settings panel enables «Sincronizza adesso» on, stated once so the panel and any
 * screen that *promises* a future sync cannot disagree about it.
 *
 * The distinction earns its keep in the person form: "these conversations will appear
 * at the next sync" is a claim about the future, and on a revoked credential -- or with
 * no mailbox at all -- it is a claim about an event that will never happen.
 */
export function useGmailSyncing(): boolean {
  const health = useGmailHealth()
  const data = health.data
  if (!data?.account) return false
  return data.account.status === 'active' && !data.missing_scopes.includes(GMAIL_READONLY_SCOPE)
}

/**
 * Invalidates the health row *and* every stored-message list. A cycle moves
 * `last_sync_at`, and any entity's Email tab may have gained messages; which ones is
 * exactly what the client cannot know, so it invalidates the whole prefix.
 */
function invalidateAfterSync(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: gmailKeys.health })
  void queryClient.invalidateQueries({ queryKey: ['gmail', 'messages'] })
}

export function useSyncGmail() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/gmail/sync')),
    onSuccess: () => invalidateAfterSync(queryClient),
  })
}

export function useDisconnectGmail() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ eliminaMessaggi }: { eliminaMessaggi: boolean }) => {
      await unwrap(
        api.DELETE('/api/gmail/account', {
          params: { query: { elimina_messaggi: eliminaMessaggi } },
        }),
      )
    },
    // Both, even when the messages were kept: the account row's `status` changed, and
    // an Email tab rendered from a cache taken before the disconnect would show a
    // history the panel now says is gone.
    onSuccess: () => invalidateAfterSync(queryClient),
  })
}

export function useSetStoreBodies() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) =>
      unwrap(api.PATCH('/api/gmail/account', { body: { gmail_store_bodies: enabled } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: gmailKeys.health }),
  })
}

/**
 * The stored mirror for one entity. No `enabled: !!id` escape hatch: the id is a
 * required route param at every call site, so there is no empty-string spelling to get
 * wrong.
 */
export function useGmailMessages(args: { entityType: GmailEntityType; entityId: string }) {
  return useQuery({
    queryKey: gmailKeys.messages(args.entityType, args.entityId),
    queryFn: () =>
      unwrap(
        api.GET('/api/gmail/messages', {
          params: { query: { entity_type: args.entityType, entity_id: args.entityId } },
        }),
      ),
  })
}
