import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, toProblem, unwrap } from '@/lib/api'
import { putVersion } from '@/features/documents/queries'
import type { ApprovalRecordBody, GmailMessage } from './approvals'
import { queryKeys } from '@/lib/query'
import { gmailKeys } from '@/features/gmail/queries'

/**
 * The proof as a document of the contract, before the approval that rests on it: a file
 * the person chose, or the text they pasted (filed as a `.txt`, which is how the archive
 * keeps any text). Two writes, the document and its first version, the way the documents
 * tab files an upload; a failure after the first leaves an empty document the person can
 * delete, which beats an approval that points at nothing.
 */
export function useFileProof() {
  return useMutation({
    mutationFn: async (args: { contractId: string; title: string; file: File }) => {
      const document = await unwrap(
        api.POST('/api/documents', {
          body: { contract_id: args.contractId, type: 'document', title: args.title, custom_fields: {} },
        }),
      )
      try {
        await putVersion(document.id, args.file)
      } catch (error) {
        throw toProblem(error)
      }
      return document
    },
  })
}

/** What recording an approval leaves stale: the days it moved, the calendar they are on,
 *  and every page of a day (the approval is on it now). */
export function useRecordApproval() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ApprovalRecordBody) =>
      unwrap(api.POST('/api/approvals', { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.workUnitsAll })
      void queryClient.invalidateQueries({ queryKey: queryKeys.workUnitAll })
      void queryClient.invalidateQueries({ queryKey: queryKeys.calendarAll })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboardAll })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
    },
  })
}

/** How many of a client's messages the picker reads: the API's own ceiling. */
const MESSAGES_LIMIT = 200

/**
 * The messages synced for one customer, newest first, asked for in that order (`order=recent`)
 * and not sorted here: the API's default lists a mailbox by thread so a conversation can be
 * followed, and it cuts at its limit, so a long history would fill the response and hide the
 * recent email that approved the days; sorting what is left cannot bring it back. Only read
 * once a customer is known and the mailbox is connected.
 */
export function useOrganizationMessages(customerId: string | undefined, enabled: boolean) {
  return useQuery({
    enabled: enabled && customerId !== undefined,
    queryKey: [...gmailKeys.messages('organization', customerId ?? ''), 'proof'],
    queryFn: (): Promise<GmailMessage[]> =>
      unwrap(
        api.GET('/api/gmail/messages', {
          params: {
            query: {
              entity_type: 'organization',
              entity_id: customerId ?? '',
              limit: MESSAGES_LIMIT,
              order: 'recent',
            },
          },
        }),
      ),
  })
}
