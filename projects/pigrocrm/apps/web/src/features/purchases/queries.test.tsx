import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import {
  useCancelPurchase,
  useCostCategories,
  useCreatePurchase,
  useDeletePurchase,
  usePurchaseList,
  usePurchases,
  useRestorePurchase,
  useUpdatePurchase,
} from './queries'

// Same reason as `features/time/queries.test.tsx`: the shared `api` client closed over
// `globalThis.fetch` at import time, so only a spy on the client's own methods
// intercepts anything.
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

function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return { wrapper, invalidate }
}

describe('usePurchases', () => {
  it('forwards its filters and asks for a full page', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(ok({ items: [], next_cursor: null }))
    renderHook(() => usePurchases({ deal_id: 'd-1', only_general: false }), { wrapper })
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(mockGet).toHaveBeenCalledWith(
      '/api/purchases',
      expect.objectContaining({
        params: { query: { deal_id: 'd-1', only_general: false, limit: 200 } },
      }),
    )
  })
})

describe('usePurchaseList', () => {
  it('asks for 50 at a time and carries the totals of the whole selection', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(
      ok({ items: [{ id: 'a' }], next_cursor: 'c2', total_count: 212, total_amount: '1464.00' }),
    )
    const { result } = renderHook(() => usePurchaseList({ status: 'registered' }), { wrapper })
    await waitFor(() => expect(result.current.totalCount).toBe(212))
    expect(result.current.totalAmount).toBe('1464.00')
    expect(result.current.hasMore).toBe(true)
    expect(mockGet).toHaveBeenCalledWith(
      '/api/purchases',
      expect.objectContaining({
        params: { query: { status: 'registered', limit: 50, cursor: undefined } },
      }),
    )
  })

  it('follows the cursor and appends the next page', async () => {
    const { wrapper } = harness()
    mockGet
      .mockReturnValueOnce(ok({ items: [{ id: 'a' }], next_cursor: 'c2', total_count: 2, total_amount: '2.00' }))
      .mockReturnValueOnce(ok({ items: [{ id: 'b' }], next_cursor: null, total_count: 2, total_amount: '2.00' }))
    const { result } = renderHook(() => usePurchaseList(), { wrapper })
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    result.current.loadMore()
    await waitFor(() => expect(result.current.items).toHaveLength(2))
    expect(mockGet).toHaveBeenLastCalledWith(
      '/api/purchases',
      expect.objectContaining({ params: { query: expect.objectContaining({ cursor: 'c2' }) } }),
    )
    expect(result.current.hasMore).toBe(false)
  })
})

describe('useCostCategories', () => {
  it('hides archived categories unless asked', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(ok([]))
    renderHook(() => useCostCategories(), { wrapper })
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(mockGet).toHaveBeenCalledWith(
      '/api/cost-categories',
      expect.objectContaining({ params: { query: { include_archived: false } } }),
    )
  })

  it('asks for the archived ones when it is told to', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(ok([]))
    renderHook(() => useCostCategories(true), { wrapper })
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(mockGet).toHaveBeenCalledWith(
      '/api/cost-categories',
      expect.objectContaining({ params: { query: { include_archived: true } } }),
    )
  })
})

describe('useCreatePurchase', () => {
  it("refreshes the list and the deal's own summary", async () => {
    const { wrapper, invalidate } = harness()
    mockPost.mockReturnValueOnce(ok({ id: 'c-1', deal_id: 'd-1' }))
    const { result } = renderHook(() => useCreatePurchase(), { wrapper })
    result.current.mutate({ deal_id: 'd-1', amount: '10.00' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['purchases', {}] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deal-time-summary', 'd-1'] })
  })

  it('leaves the deal summaries alone for a general purchase, which belongs to no deal', async () => {
    const { wrapper, invalidate } = harness()
    mockPost.mockReturnValueOnce(ok({ id: 'c-2', deal_id: null }))
    const { result } = renderHook(() => useCreatePurchase(), { wrapper })
    result.current.mutate({ amount: '10.00' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['purchases', {}] })
    // The lists and the page of a purchase (`['purchase']`), and no deal summary.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['purchase'] })
    expect(invalidate).toHaveBeenCalledTimes(2)
  })
})

describe('useUpdatePurchase', () => {
  it('patches the purchase it was built for', async () => {
    const { wrapper } = harness()
    mockPatch.mockReturnValueOnce(ok({ id: 'c-1', deal_id: 'd-1' }))
    const { result } = renderHook(() => useUpdatePurchase('c-1'), { wrapper })
    result.current.mutate({ amount: '12.00' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/purchases/{purchase_id}',
      expect.objectContaining({ params: { path: { purchase_id: 'c-1' } }, body: { amount: '12.00' } }),
    )
  })
})

describe('useDeletePurchase', () => {
  it('deletes by id and refreshes the list', async () => {
    const { wrapper, invalidate } = harness()
    mockDelete.mockReturnValueOnce(ok(undefined))
    const { result } = renderHook(() => useDeletePurchase(), { wrapper })
    result.current.mutate('c-1')
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockDelete).toHaveBeenCalledWith(
      '/api/purchases/{purchase_id}',
      expect.objectContaining({ params: { path: { purchase_id: 'c-1' } } }),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['purchases', {}] })
  })
})

describe('useRestorePurchase', () => {
  it('posts to the restore route and refreshes the list and the deal summary', async () => {
    const { wrapper, invalidate } = harness()
    mockPost.mockReturnValueOnce(ok({ id: 'c-1', deal_id: 'd-1' }))
    const { result } = renderHook(() => useRestorePurchase(), { wrapper })
    result.current.mutate('c-1')
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockPost).toHaveBeenCalledWith(
      '/api/purchases/{purchase_id}/restore',
      expect.objectContaining({ params: { path: { purchase_id: 'c-1' } } }),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['purchases', {}] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deal-time-summary', 'd-1'] })
  })
})

describe('useCancelPurchase', () => {
  it('posts to the cancel route and refreshes the list', async () => {
    const { wrapper, invalidate } = harness()
    mockPost.mockReturnValueOnce(ok({ id: 'c-1', deal_id: null }))
    const { result } = renderHook(() => useCancelPurchase(), { wrapper })
    result.current.mutate('c-1')
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockPost).toHaveBeenCalledWith(
      '/api/purchases/{purchase_id}/cancel',
      expect.objectContaining({ params: { path: { purchase_id: 'c-1' } } }),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['purchases', {}] })
  })
})
