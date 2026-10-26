import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'

export type ApprovalRequestDay = components['schemas']['ApprovalRequestDay']
export type ApprovalRequestMessage = components['schemas']['ApprovalRequestMessage']
export type ApprovalRequestPrepareBody = components['schemas']['ApprovalRequestPrepare']
export type ApprovalRequestPrepared = components['schemas']['ApprovalRequestPrepared']

/** The text a request would carry for these days: written by the API, stored nowhere. */
export function usePreviewRequest(contractId: string) {
  return useMutation({
    mutationFn: (days: ApprovalRequestDay[]) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/approval-request/preview', {
          params: { path: { contract_id: contractId } },
          body: { days },
        }),
      ),
  })
}

/** Records the named days as proposed and files the message as a draft of the client, in
 *  one request. It does not send: sending is the draft's own route, pressed after reading. */
export function usePrepareRequest(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ApprovalRequestPrepareBody) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/approval-request', {
          params: { path: { contract_id: contractId } },
          body,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['work-units'] })
      void queryClient.invalidateQueries({ queryKey: ['calendario'] })
      void queryClient.invalidateQueries({ queryKey: ['gmail'] })
    },
  })
}
