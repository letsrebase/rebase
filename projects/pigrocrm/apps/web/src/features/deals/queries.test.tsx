import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchAllDeals, useMoveDeal } from './queries'
import type { Deal } from './queries'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/query'

vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const mockGet = vi.spyOn(api, 'GET')
const mockPatch = vi.spyOn(api, 'PATCH')

beforeEach(() => {
  mockGet.mockReset()
  mockPatch.mockReset()
  vi.mocked(toast.error).mockReset()
})

function page(items: Partial<Deal>[], nextCursor: string | null) {
  return Promise.resolve({
    data: { items, next_cursor: nextCursor },
    response: new Response(null, { status: 200 }),
  })
}

function deal(id: string): Partial<Deal> {
  return { id, nome: `Deal ${id}`, pipeline_stage_id: 's1' }
}

describe('fetchAllDeals', () => {
  it('resolves in one request when the first page already has no next_cursor', async () => {
    mockGet.mockReturnValueOnce(page([deal('a'), deal('b')], null))

    const result = await fetchAllDeals({})

    expect(result).toEqual({ items: [deal('a'), deal('b')], truncated: false })
    expect(mockGet).toHaveBeenCalledTimes(1)
  })

  /**
   * The exact bug this function exists to fix: `GET /api/deals` defaults to 50
   * results per page and caps at 200 (`DealListQuery.limit`, deals/schemas.py).
   * A `useDeals` that stopped at the first page -- what the brief's own sample
   * did -- would silently drop every deal past the first page. This proves the
   * aggregation actually walks the cursor rather than trusting a single
   * response.
   */
  it('walks every page until next_cursor is null, concatenating all of them', async () => {
    mockGet
      .mockReturnValueOnce(page([deal('a'), deal('b')], 'b'))
      .mockReturnValueOnce(page([deal('c')], 'c'))
      .mockReturnValueOnce(page([deal('d')], null))

    const result = await fetchAllDeals({})

    expect(result.items.map((d) => d.id)).toEqual(['a', 'b', 'c', 'd'])
    expect(result.truncated).toBe(false)
    expect(mockGet).toHaveBeenCalledTimes(3)
  })

  it('requests the maximum page size on every page, not the server default', async () => {
    mockGet.mockReturnValueOnce(page([], null))

    await fetchAllDeals({})

    expect(mockGet).toHaveBeenCalledWith(
      '/api/deals',
      expect.objectContaining({ params: expect.objectContaining({ query: expect.objectContaining({ limit: 200 }) }) }),
    )
  })

  it('forwards the cursor from one page as the next request’s cursor', async () => {
    mockGet.mockReturnValueOnce(page([deal('a')], 'a')).mockReturnValueOnce(page([deal('b')], null))

    await fetchAllDeals({})

    const secondCallArgs = mockGet.mock.calls[1]
    expect(secondCallArgs?.[1]).toEqual(
      expect.objectContaining({ params: expect.objectContaining({ query: expect.objectContaining({ cursor: 'a' }) }) }),
    )
  })

  it('forwards search/customer_id/stage_id filters unchanged on every page', async () => {
    mockGet.mockReturnValueOnce(page([], null))

    await fetchAllDeals({ search: 'sito', customer_id: 'cust-1', stage_id: 'stage-1' })

    expect(mockGet).toHaveBeenCalledWith(
      '/api/deals',
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            search: 'sito',
            customer_id: 'cust-1',
            stage_id: 'stage-1',
          }),
        }),
      }),
    )
  })

  /**
   * The one safety valve: a tenant so large it never runs out of `next_cursor`
   * within `MAX_PAGES` gets `truncated: true` instead of looping forever, or
   * worse, silently stopping with no way for the caller to know. Not a state
   * this product's own target user is expected to reach -- see the constant's
   * own comment in queries.ts -- but if it ever happens, this is what the UI's
   * truncation banner (routes/app/deal/index.tsx, routes/app/deal/lista.tsx)
   * keys off.
   */
  it('reports truncated and stops after the page cap, never looping indefinitely', async () => {
    // Every page claims there is more -- if the loop had no cap, this would
    // hang the test (and the real app) forever.
    mockGet.mockImplementation(() => page([deal('x')], 'always-more'))

    const result = await fetchAllDeals({})

    expect(result.truncated).toBe(true)
    expect(mockGet).toHaveBeenCalledTimes(100)
    expect(result.items).toHaveLength(100)
  })
})

function createWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

function newClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function delayed<T>(value: T, ms: number): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

// Cast `as never`, not left inferred: `api.PATCH` is one overloaded function
// covering every PATCH path in the generated `paths` type, and `vi.spyOn`
// can only assign the resulting mock a single call signature -- whichever
// overload TypeScript happens to settle on for an un-narrowed spy (observed
// here resolving to an unrelated endpoint's request/response shape, not
// `/api/deals/{deal_id}/stage`'s own). `unwrap` only ever reads `data`/
// `error`/`response` off whatever `api.PATCH` resolves to, so the real
// runtime shape is exactly what these two helpers build; the cast says so
// honestly rather than fighting the overload set to make TS re-derive it,
// the same idiom `queries.ts` itself uses for the opposite direction
// (`body as unknown as DealCreateBody`).
function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

describe('useMoveDeal', () => {
  /**
   * The exact regression a fix round caught live: `useMoveDeal` is one
   * `useMutation()` instance shared by the whole Kanban board, and dragging a
   * second card before the first PATCH settles calls `.mutate()` again on
   * that same observer. `@tanstack/query-core`'s `MutationObserver` re-points
   * itself at the new call when that happens -- which is why the FIRST call's
   * own per-call `onError` (passed directly to that `.mutate()` invocation)
   * never fires once the first mutation actually settles, even though it
   * really did fail. Reproduced with a proxy delaying the PATCH by 2.5s
   * before this fix: a failing move followed by a quicker succeeding one
   * rolled back silently, with no toast at any point. Reversing the order
   * (fail second) made the toast appear, which is exactly why a single-move
   * test cannot tell a correct fix from the original bug -- this test drives
   * two overlapping mutations on purpose, first-slow-and-failing,
   * second-fast-and-succeeding, matching the reproduction exactly.
   */
  it('still toasts the first move’s own failure even though a second move overtook it before it settled', async () => {
    mockPatch
      .mockImplementationOnce(() =>
        delayed(failed({ code: 'not_found', detail: 'deal a not found' }, 404), 40),
      )
      .mockImplementationOnce(() => delayed(ok({ id: 'b', pipeline_stage_id: 's2' }), 10))

    const { result } = renderHook(() => useMoveDeal(), { wrapper: createWrapper(newClient()) })

    const firstPerCallOnError = vi.fn()
    const secondPerCallOnSuccess = vi.fn()

    act(() => {
      result.current.mutate({ dealId: 'a', stageId: 's1' }, { onError: firstPerCallOnError })
    })
    act(() => {
      result.current.mutate({ dealId: 'b', stageId: 's2' }, { onSuccess: secondPerCallOnSuccess })
    })

    await waitFor(() => expect(secondPerCallOnSuccess).toHaveBeenCalled())

    // Documents the library mechanism this hook's fix routes around: the
    // FIRST call's own per-call onError is silently dropped once the second
    // call takes over the observer -- this is why the toast cannot live at
    // the call site (see routes/app/deal/index.tsx's `onMove`, which no
    // longer passes one).
    expect(firstPerCallOnError).not.toHaveBeenCalled()
    // ...but the toast still appears, because `useMoveDeal`'s own `onError`
    // is baked into each mutation's own options at `.mutate()` time, not into
    // whichever per-call options the observer currently happens to be
    // pointing at.
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('deal a not found'))
  })

  it('does not toast anything when the move succeeds', async () => {
    mockPatch.mockReturnValueOnce(Promise.resolve(ok({ id: 'a', pipeline_stage_id: 's2' })))

    const { result } = renderHook(() => useMoveDeal(), { wrapper: createWrapper(newClient()) })

    act(() => {
      result.current.mutate({ dealId: 'a', stageId: 's2' })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(toast.error).not.toHaveBeenCalled()
  })

  /**
   * Observed live: a pipeline stage deleted server-side (admin-only,
   * `PipelineService.delete`) kept rendering as a phantom Kanban column,
   * with whatever deals it still had, until `useStages`' own 30s `staleTime`
   * happened to lapse. A move is the one event on this screen that already
   * touches the pipeline, so it is the natural point to also refresh what
   * the pipeline itself looks like.
   */
  it('invalidates the pipeline-stages query on settle', async () => {
    const client = newClient()
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')
    mockPatch.mockReturnValueOnce(Promise.resolve(ok({ id: 'a', pipeline_stage_id: 's2' })))

    const { result } = renderHook(() => useMoveDeal(), { wrapper: createWrapper(client) })

    act(() => {
      result.current.mutate({ dealId: 'a', stageId: 's2' })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: queryKeys.stages }),
    )
  })
})
