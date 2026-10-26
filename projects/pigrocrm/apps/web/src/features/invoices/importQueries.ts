import { useQueryClient } from '@tanstack/react-query'
import { fetchWithRefresh, toProblem } from '@/lib/api'
import type { components } from '@/lib/api-types'
import type { ConfirmRow, ImportDecisions, ReviewRow } from './importPlan'

/**
 * The invoices page's import: the two multipart routes of REB-758.
 *
 * Outside `openapi-fetch`, for the reason `putVersion` in `features/documents/queries.ts`
 * documents: the typed client cannot send a `FormData` body. Through `fetchWithRefresh`,
 * so the cookie travels, a space's prefix is applied and an expired session is renewed
 * once instead of failing with «Autenticazione richiesta». No `Content-Type` is set by
 * hand: the browser appends the multipart boundary itself.
 *
 * Nothing is stored between the two calls. The review names every file by its SHA-256 and
 * every invoice's customer by a `party_key`; the confirmation is sent the same files
 * again, with the decisions keyed by that party, and re-reads them from their own bytes:
 * it never trusts the review it follows.
 */

type ReviewResult = components['schemas']['UploadedInvoiceReviewResult']
type ConfirmResult = components['schemas']['UploadedInvoiceConfirmResult']

async function send<T>(path: string, files: readonly File[], fields: Record<string, string>) {
  const body = new FormData()
  for (const file of files) body.append('file', file, file.name)
  for (const [name, value] of Object.entries(fields)) body.append(name, value)
  const response = await fetchWithRefresh(path, { method: 'POST', body })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) throw toProblem(payload, response.status)
  return payload as T
}

export async function reviewImportFiles(files: readonly File[]): Promise<ReviewRow[]> {
  const result = await send<ReviewResult>('/api/invoices/import/upload/review', files, {})
  return result.righe
}

export async function confirmImportFiles(
  files: readonly File[],
  decisions: ImportDecisions,
): Promise<ConfirmRow[]> {
  const result = await send<ConfirmResult>('/api/invoices/import/upload/confirm', files, {
    decisioni: JSON.stringify(decisions),
  })
  return result.righe
}

/**
 * What an import that wrote something makes stale: the lists of invoices, the customers
 * it may have created and the documents it filed the XML under. Called once at the end,
 * not per batch, so the list behind the dialog does not reload under a person reading
 * the result.
 */
export function useInvalidateAfterImport() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['invoices'] })
    void queryClient.invalidateQueries({ queryKey: ['invoices-list'] })
    // The year's register and the room under the ceilings read the same invoices, as in
    // `useInvoiceInvalidation`.
    void queryClient.invalidateQueries({ queryKey: ['year-overview'] })
    void queryClient.invalidateQueries({ queryKey: ['ceilings'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
  }
}
