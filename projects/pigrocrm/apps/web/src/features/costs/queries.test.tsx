import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import {
  useCostCategories,
  useCosts,
  useCreateCost,
  useDeleteCost,
  useUpdateCost,
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

describe('useCosts', () => {
  it('forwards its filters and asks for a full page', async () => {
    const { wrapper } = harness()
    mockGet.mockReturnValueOnce(ok({ items: [], next_cursor: null }))
    renderHook(() => useCosts({ deal_id: 'd-1', only_general: false }), { wrapper })
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(mockGet).toHaveBeenCalledWith(
      '/api/costs',
      expect.objectContaining({
        params: { query: { deal_id: 'd-1', only_general: false, limit: 200 } },
      }),
    )
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

describe('useCreateCost', () => {
  it("refreshes the list and the deal's own summary", async () => {
    const { wrapper, invalidate } = harness()
    mockPost.mockReturnValueOnce(ok({ id: 'c-1', deal_id: 'd-1' }))
    const { result } = renderHook(() => useCreateCost(), { wrapper })
    result.current.mutate({ deal_id: 'd-1', amount: '10.00' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['costs', {}] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['deal-time-summary', 'd-1'] })
  })

  it('leaves the deal summaries alone for a general expense, which belongs to no deal', async () => {
    const { wrapper, invalidate } = harness()
    mockPost.mockReturnValueOnce(ok({ id: 'c-2', deal_id: null }))
    const { result } = renderHook(() => useCreateCost(), { wrapper })
    result.current.mutate({ amount: '10.00' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['costs', {}] })
    expect(invalidate).toHaveBeenCalledTimes(1)
  })
})

describe('useUpdateCost', () => {
  it('patches the cost it was built for', async () => {
    const { wrapper } = harness()
    mockPatch.mockReturnValueOnce(ok({ id: 'c-1', deal_id: 'd-1' }))
    const { result } = renderHook(() => useUpdateCost('c-1'), { wrapper })
    result.current.mutate({ amount: '12.00' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockPatch).toHaveBeenCalledWith(
      '/api/costs/{cost_id}',
      expect.objectContaining({ params: { path: { cost_id: 'c-1' } }, body: { amount: '12.00' } }),
    )
  })
})

describe('useDeleteCost', () => {
  it('deletes by id and refreshes the list', async () => {
    const { wrapper, invalidate } = harness()
    mockDelete.mockReturnValueOnce(ok(undefined))
    const { result } = renderHook(() => useDeleteCost(), { wrapper })
    result.current.mutate('c-1')
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockDelete).toHaveBeenCalledWith(
      '/api/costs/{cost_id}',
      expect.objectContaining({ params: { path: { cost_id: 'c-1' } } }),
    )
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['costs', {}] })
  })
})
