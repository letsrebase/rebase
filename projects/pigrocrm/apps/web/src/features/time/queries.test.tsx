import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import {
  timeReportXlsxPath,
  useDealRates,
  useDealTimeSummary,
  useDeleteTimeEntry,
  useLogTime,
  useTimeEntries,
  useUpdateTimeEntry,
} from './queries'

// `openapi-fetch`'s client captures `globalThis.fetch` into a closure once, at
// `createClient()` time -- so stubbing `globalThis.fetch` here would never intercept a
// request made through the shared `api` client. Every query-layer test in this codebase
// (features/deals/queries.test.tsx, features/documents/queries.test.tsx) spies on
// `api.GET`/`api.POST`/... directly for that reason, and this file follows the same
// convention.
const mockGet = vi.spyOn(api, 'GET')
const mockPost = vi.spyOn(api, 'POST')
const mockPatch = vi.spyOn(api, 'PATCH')
const mockDelete = vi.spyOn(api, 'DELETE')

afterEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
  mockPatch.mockReset()
  mockDelete.mockReset()
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

/** One client per `renderHook`, handed back so a mutation test can watch what the
 *  hook's `onSuccess` invalidates -- the whole point of these modules is that an hour
 *  written in one place refreshes every screen that shows it. */
function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return { wrapper, invalidate }
}

describe('useTimeEntries', () => {
  it('forwards its filters and asks for a full page', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(ok({ items: [], next_cursor: null }))
    renderHook(() => useTimeEntries({ deal_id: 'd-1', da: '2026-08-01' }), { wrapper })
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(mockGet).toHaveBeenCalledWith(
      '/api/time-entries',
      expect.objectContaining({
        params: { query: { deal_id: 'd-1', da: '2026-08-01', limit: 200 } },
      }),
    )
  })

  it('surfaces a failure as an error, never as an empty list', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(failed({ code: 'forbidden', detail: 'Non autorizzato' }, 403))
    const { result } = renderHook(() => useTimeEntries({}), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
  })
})

describe('useDealTimeSummary', () => {
  it('asks for the deal it was given', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(ok({ deal_id: 'd-1' }))
    renderHook(() => useDealTimeSummary('d-1'), { wrapper })
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(mockGet).toHaveBeenCalledWith(
      '/api/deals/{deal_id}/time-summary',
      expect.objectContaining({ params: { path: { deal_id: 'd-1' } } }),
    )
  })
})

describe('useDealRates', () => {
  it('never fires without a real user id', () => {
    // An empty path/query segment does not match the route: Starlette's trailing-slash
    // redirect lands on the *list* endpoint with an absolute URL that escapes the Vite
    // dev proxy, and still resolves 200 with nothing to show. Disabling the query is
    // the structural fix; this test is what keeps a refactor from undoing it.
    const { wrapper } = harness()
    const { result } = renderHook(() => useDealRates('d-1', undefined), { wrapper })
    expect(mockGet).not.toHaveBeenCalled()
    expect(result.current.fetchStatus).toBe('idle')
  })

  it('asks for the deal and the user together', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(ok({ rate: '80.00', rate_source: 'deal' }))
    renderHook(() => useDealRates('d-1', 'u-1'), { wrapper })
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(mockGet).toHaveBeenCalledWith(
      '/api/deals/{deal_id}/rates',
      expect.objectContaining({
        params: { path: { deal_id: 'd-1' }, query: { user_id: 'u-1' } },
      }),
    )
  })
})

describe('useLogTime', () => {
  it('refreshes the list, the deal summary and the deal timeline', async () => {
    const { wrapper, invalidate } = harness()
    mockPost.mockReturnValueOnce(ok({ id: 'e-1', deal_id: 'd-1' }))
    const { result } = renderHook(() => useLogTime(), { wrapper })
    result.current.mutate({ deal_id: 'd-1', hours: '2.00' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockPost).toHaveBeenCalledWith(
      '/api/time-entries',
      expect.objectContaining({ body: { deal_id: 'd-1', hours: '2.00' } }),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['time-entries', {}] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deal-time-summary', 'd-1'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['timeline', 'deal', 'd-1'] })
  })
})

describe('useUpdateTimeEntry', () => {
  it('patches the entry it was built for and refreshes its deal', async () => {
    const { wrapper, invalidate } = harness()
    mockPatch.mockReturnValueOnce(ok({ id: 'e-1', deal_id: 'd-2' }))
    const { result } = renderHook(() => useUpdateTimeEntry('e-1'), { wrapper })
    result.current.mutate({ hours: '3.50' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/time-entries/{entry_id}',
      expect.objectContaining({ params: { path: { entry_id: 'e-1' } }, body: { hours: '3.50' } }),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deal-time-summary', 'd-2'] })
  })
})

describe('useDeleteTimeEntry', () => {
  it('takes the deal id from the caller, since a 204 carries no body to read it from', async () => {
    const { wrapper, invalidate } = harness()
    mockDelete.mockReturnValueOnce(ok(undefined))
    const { result } = renderHook(() => useDeleteTimeEntry(), { wrapper })
    result.current.mutate({ entryId: 'e-1', dealId: 'd-1' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockDelete).toHaveBeenCalledWith(
      '/api/time-entries/{entry_id}',
      expect.objectContaining({ params: { path: { entry_id: 'e-1' } } }),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deal-time-summary', 'd-1'] })
  })
})

describe('timeReportXlsxPath', () => {
  it('builds the API path of the XLSX, to download through the refreshing fetch', () => {
    expect(timeReportXlsxPath('d-1', '2026-08')).toBe(
      '/api/deals/d-1/time-report?month=2026-08',
    )
  })
})
