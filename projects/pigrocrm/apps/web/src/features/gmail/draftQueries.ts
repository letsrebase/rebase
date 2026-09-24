import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { gmailKeys, type GmailEntityType } from './queries'

export type EmailDraftRead = components['schemas']['EmailDraftRead']
export type EmailDraftAttachment = components['schemas']['EmailDraftAttachment']
export type EmailDraftPage = components['schemas']['EmailDraftPage']
export type SendState = EmailDraftRead['send_state']

/**
 * The states from which a draft can be sent, and deleted. The same set as
 * `EDITABLE_SEND_STATES` in `packages/core/src/pigrocrm/core/gmail/schemas.py`: the
 * server sends and deletes only from these, and a copy that drifted would offer an
 * «Invia» the API refuses, or hide one it would have accepted.
 *
 * `fallito` is in it on purpose (spec 6.3(a)): a refused send leaves the draft intact
 * with the error beside it, and pressing «Invia» again is how it leaves.
 */
export const SENDABLE_STATES: readonly SendState[] = ['bozza', 'fallito']

export function isSendable(state: SendState): boolean {
  return SENDABLE_STATES.includes(state)
}

/**
 * Keyed locally, like `gmailKeys`: `['email-drafts', ...]` is a root nobody else writes
 * under, so an outcome can invalidate every entity's drafts without guessing which lists
 * happen to be cached.
 */
export const draftKeys = {
  all: ['email-drafts'] as const,
  forEntity: (entityType: string, entityId: string) =>
    ['email-drafts', 'entity', entityType, entityId] as const,
}

/** How often the list is read again while a draft is `in_invio`: a send in flight on
 *  another tab, or one whose request died after the claim, settles on the server and
 *  nowhere else, and a card that never read it again would say «Invio in corso» forever. */
const IN_FLIGHT_POLL_MS = 10_000

/**
 * Every draft filed against one entity, newest first. What the Email tab reads to show
 * the drafts that have not left above the correspondence that has.
 *
 * The API's ceiling rather than its default of 50: the list carries the sent drafts too
 * (there is one filter per state and no "not sent"), and sent drafts only accumulate, so
 * on a customer with a long history the default would push an old unsent one off the
 * page without a word.
 */
export function useDraftsForEntity(args: { entityType: GmailEntityType; entityId: string }) {
  return useQuery({
    queryKey: draftKeys.forEntity(args.entityType, args.entityId),
    queryFn: () =>
      unwrap(
        api.GET('/api/email-drafts', {
          params: {
            query: { entity_type: args.entityType, entity_id: args.entityId, limit: 200 },
          },
        }),
      ),
    refetchInterval: (query) =>
      query.state.data?.items.some((draft) => draft.send_state === 'in_invio')
        ? IN_FLIGHT_POLL_MS
        : false,
  })
}

/**
 * Writes what the server just said about one draft into the list the card was rendered
 * from, before the list is read again.
 *
 * Without it, the card is stale for one round trip after every outcome: the send's
 * answer arrives, the pending state drops, and «Bozza» with an enabled «Invia» is back on
 * screen until the refetch lands -- the one moment a second press is most likely. The
 * refetch that follows (`invalidateOutcome`) still brings the rest of the row, such as
 * the server's `last_error`.
 */
export function patchCachedDraft(
  queryClient: QueryClient,
  draft: Pick<EmailDraftRead, 'id' | 'entity_type' | 'entity_id'>,
  patch: Partial<EmailDraftRead>,
) {
  queryClient.setQueryData<EmailDraftPage>(
    draftKeys.forEntity(draft.entity_type, draft.entity_id),
    (page) =>
      page && {
        ...page,
        items: page.items.map((item) => (item.id === draft.id ? { ...item, ...patch } : item)),
      },
  )
}

/**
 * The drafts, whatever happened, the correspondence, and the mailbox's own state: a send
 * that answered writes an outbound message row the Email tab shows, a send that did
 * *not* answer has still moved the draft to `fallito` or `incerto` on the server, and a
 * token refresh that failed may have marked the mailbox revoked. Settled rather than
 * succeeded is the point: invalidating only on success would leave a refused draft on
 * screen as «Bozza», inviting the same press again.
 */
function invalidateOutcome(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: draftKeys.all })
  void queryClient.invalidateQueries({ queryKey: ['gmail', 'messages'] })
  void queryClient.invalidateQueries({ queryKey: gmailKeys.health })
}

/** A hard delete, and only of a draft that has not started to leave: the server refuses
 *  one in flight or sent, and this list never offers it for those. Settled, so a refused
 *  delete (the draft started to leave meanwhile) reads the row again too. */
export function useDeleteDraft() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (draftId: string) => {
      await unwrap(
        api.DELETE('/api/email-drafts/{draft_id}', {
          params: { path: { draft_id: draftId } },
        }),
      )
      return draftId
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: draftKeys.all }),
  })
}

/**
 * The one call in the product with no idempotency key.
 *
 * No `retry`, and there must never be one: Gmail offers no idempotency key, so a second
 * attempt is a second email in somebody's client's inbox. React Query does not retry
 * mutations by default and this relies on that; what `PendingDrafts` restates is that a
 * *person* cannot press it twice either.
 */
export function useSendDraft() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (draftId: string) =>
      unwrap(
        api.POST('/api/email-drafts/{draft_id}/send', {
          params: { path: { draft_id: draftId } },
        }),
      ),
    onSettled: () => invalidateOutcome(queryClient),
  })
}

/**
 * «Verifica»: resolves an unknown outcome by *asking Gmail*, never by sending again.
 *
 * A separate mutation from `useSendDraft` for the same reason it is a separate endpoint
 * (`routers/email_drafts.py`): a "riprova" that re-posted the send would offer the
 * recipient a second copy of a message that may already be in their inbox, which is the
 * exact defect `incerto` exists to name.
 */
export function useReconcileDraft() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (draftId: string) =>
      unwrap(
        api.POST('/api/email-drafts/{draft_id}/reconcile', {
          params: { path: { draft_id: draftId } },
        }),
      ),
    onSettled: () => invalidateOutcome(queryClient),
  })
}
