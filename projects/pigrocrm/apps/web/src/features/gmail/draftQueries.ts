import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { gmailKeys, type GmailEntityType } from './queries'

export type EmailDraftRead = components['schemas']['EmailDraftRead']
export type EmailDraftAttachment = components['schemas']['EmailDraftAttachment']
export type EmailDraftPage = components['schemas']['EmailDraftPage']
/**
 * The page as the tab holds it: the server's answer, and which read it was, numbered when
 * the request *started*. Kept in the data rather than read off the query's
 * `dataUpdatedAt`, which `patchCachedDraft` moves too: a local write about one draft is
 * not a read of the others, and a card waiting to learn what happened to its own
 * unanswered send must not take it for one. A counter rather than a clock, so two reads
 * in the same millisecond are still two.
 */
export type DraftList = EmailDraftPage & { readSeq: number }

let readsStarted = 0

/** The number of the last list read that has started. A card that records it when its
 *  send fails, and asks for a new read right after, knows that only a page numbered
 *  above it was read after the failure. */
export function lastReadStarted(): number {
  return readsStarted
}
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
 * The drafts filed against one entity that have not left, newest first: what the Email
 * tab shows above the correspondence that has.
 *
 * `unsent` is the server's filter and not a client-side one on purpose: sent drafts only
 * accumulate, and asking for every state then dropping the sent ones let a page of them
 * push an older unsent draft off the list without a word. The API's ceiling, 200, is a
 * number of drafts still waiting on a person, which no entity comes near.
 */
export function useDraftsForEntity(args: { entityType: GmailEntityType; entityId: string }) {
  return useQuery({
    queryKey: draftKeys.forEntity(args.entityType, args.entityId),
    queryFn: async (): Promise<DraftList> => {
      // Numbered before the request leaves: a read that was already in flight when a send
      // failed may carry the row from before the claim, and must not count as a re-read.
      const readSeq = ++readsStarted
      const page = await unwrap(
        api.GET('/api/email-drafts', {
          params: {
            query: {
              entity_type: args.entityType,
              entity_id: args.entityId,
              unsent: true,
              limit: 200,
            },
          },
        }),
      )
      return { ...page, readSeq }
    },
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
  queryClient.setQueryData<DraftList>(
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
