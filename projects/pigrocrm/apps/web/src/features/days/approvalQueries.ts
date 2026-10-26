import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, toProblem, unwrap } from '@/lib/api'
import { putVersion } from '@/features/documents/queries'
import type { ApprovalRecordBody } from './approvals'

/**
 * The proof as a document of the contract, before the approval that rests on it: a file
 * the person chose, or the text they pasted (filed as a `.txt`, which is how the archive
 * keeps any text). Two writes, the document and its first version, the way the documents
 * tab files an upload; a failure after the first leaves an empty document the person can
 * delete, which beats an approval that points at nothing.
 */
export function useFileProof() {
  return useMutation({
    mutationFn: async (args: { contractId: string; titolo: string; file: File }) => {
      const document = await unwrap(
        api.POST('/api/documents', {
          body: { contract_id: args.contractId, tipo: 'documento', titolo: args.titolo, custom_fields: {} },
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
      void queryClient.invalidateQueries({ queryKey: ['work-units'] })
      void queryClient.invalidateQueries({ queryKey: ['work-unit'] })
      void queryClient.invalidateQueries({ queryKey: ['calendario'] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })
}
