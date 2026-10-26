import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { downloadDocument } from '@/features/documents/queries'
import { queryKeys } from '@/lib/query'

/** Wire shapes straight from the generated schema. `Decimal`s arrive as strings. */
export type BillableDays = components['schemas']['BillableDays']
export type BillableLine = components['schemas']['BillableLine']
export type ContractRegister = components['schemas']['ContractRegister']
export type RegisterEntry = components['schemas']['RegisterEntry']

/** The first and last day of an `AAAA-MM` month, as the inclusive `da` and `a` the API
 *  takes. Built from the parts, never through `toISOString()`: a month's last day moved
 *  to UTC lands on the wrong date east of Greenwich (`@/lib/dates`). */
export function monthBounds(month: string): { da: string; a: string } {
  const [year, number] = month.split('-').map(Number)
  const last = new Date(year ?? 0, number ?? 1, 0).getDate()
  return { da: `${month}-01`, a: `${month}-${String(last).padStart(2, '0')}` }
}

/** The ready days, all of them or those of one period (`da`/`a`, inclusive). */
export function useBillableDays(contractId: string, period: { da?: string; a?: string } = {}) {
  return useQuery({
    queryKey: queryKeys.contractBillableDays(contractId, period),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/billable-days', {
          params: { path: { contract_id: contractId }, query: period },
        }),
      ),
  })
}

/** The draft with the contract's ready days on it. What it moves is wider than the section
 *  that asked: the days are no longer ready, the register gains them, the invoices list has
 *  a new row and the contract's own timeline says so. */
export function useInvoiceDays(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (period: { da?: string; a?: string }) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/invoice-days', {
          params: { path: { contract_id: contractId } },
          body: period,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.contract(contractId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.invoices() })
      // The days list and the calendar read the same days, now `fatturato`.
      void queryClient.invalidateQueries({ queryKey: ['work-units'] })
      void queryClient.invalidateQueries({ queryKey: ['calendario'] })
    },
  })
}

export function useContractRegister(contractId: string, period: { da: string; a: string }) {
  return useQuery({
    queryKey: queryKeys.contractRegister(contractId, period),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/register', {
          params: { path: { contract_id: contractId }, query: period },
        }),
      ),
  })
}

/** The CSV is a working copy the browser downloads itself: a same-origin URL, so the
 *  session cookie travels with it and no token goes into a query string. */
export function registerCsvUrl(contractId: string, period: { da: string; a: string }): string {
  const query = new URLSearchParams(period)
  return `/api/contracts/${contractId}/register/csv?${query.toString()}`
}

/** The PDF is an archived artefact, so it is two steps and not a link: render (which
 *  archives a new version as the customer's document), then download that document through
 *  the one path that carries authorisation. The same shape as the timesheet's. */
export function useRenderRegisterPdf(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (period: { da: string; a: string }) => {
      const document = await unwrap(
        api.POST('/api/contracts/{contract_id}/register/pdf', {
          params: { path: { contract_id: contractId }, query: period },
        }),
      )
      await downloadDocument(document.id)
      return document
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
    },
  })
}
