import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useDeals, useMoveDeal } from '@/features/deals/queries'
import { spaceModules } from '@/lib/modules.testing'
import { useFirstSteps, useSpaceEmpty, useSpaceHasData } from './firstSteps'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u-1', name: 'Giulia Demo', role: 'admin' } }),
}))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const DEAL = { id: 'd-1', name: 'Progetto', pipeline_stage_id: 's-lead' }

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

/** The reads the hooks make about the work (the space's modules are read too, REB-953). */
function workReads() {
  return (vi.mocked(api.GET).mock.calls as unknown as [string, unknown][]).filter(
    ([path]) => path !== '/api/settings/modules',
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    ok({ items: path === '/api/deals' ? [DEAL] : [], next_cursor: null })) as never)
  vi.mocked(api.PATCH).mockImplementation((() => ok({ ...DEAL, pipeline_stage_id: 's-offerta' })) as never)
})

describe('useSpaceEmpty', () => {
  it('says the space has work when any of the four reads finds a row, and none otherwise', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useSpaceEmpty(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.spaceEmpty).toBe(false))

    vi.mocked(api.GET).mockImplementation((() => ok({ items: [], next_cursor: null })) as never)
    const empty = renderHook(() => useSpaceEmpty(), {
      wrapper: wrapper(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    })
    await waitFor(() => expect(empty.result.current.spaceEmpty).toBe(true))
  })

  /**
   * The reads live under the keys the rest of the app invalidates, and a board's optimistic
   * move walks every entry under `['deals']` expecting a page. An entry holding a boolean
   * made `useMoveDeal` throw in `onMutate` and the card never moved (REB-873: the shell
   * mounts these reads on every page, so the entry is always there).
   */
  it('leaves a page, not a boolean, under every key it shares with the lists', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useSpaceEmpty(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.spaceEmpty).toBe(false))

    for (const prefix of ['customers', 'deals', 'time-entries', 'documents']) {
      const entries = client.getQueriesData<{ items: unknown[] }>({ queryKey: [prefix] })
      expect(entries.length, prefix).toBeGreaterThan(0)
      for (const [, data] of entries) expect(Array.isArray(data?.items), prefix).toBe(true)
    }
  })

  it('does not stop a card move while the shell holds the read', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(
      () => ({ space: useSpaceEmpty(), board: useDeals({}), move: useMoveDeal() }),
      { wrapper: wrapper(client) },
    )
    await waitFor(() => expect(result.current.space.spaceEmpty).toBe(false))
    await waitFor(() => expect(result.current.board.data).toBeDefined())

    result.current.move.mutate({ dealId: 'd-1', stageId: 's-offerta' })
    await waitFor(() => expect(result.current.move.isSuccess).toBe(true))
    expect(api.PATCH).toHaveBeenCalled()
  })
})

describe('useSpaceHasData', () => {
  it('is false for an empty space and true once any read finds work', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok({ items: [], next_cursor: null })) as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useSpaceHasData(), { wrapper: wrapper(client) })
    await waitFor(() => expect(workReads()).toHaveLength(4))
    await waitFor(() => expect(client.isFetching()).toBe(0))
    expect(result.current).toBe(false)

    vi.mocked(api.GET).mockImplementation(((path: string) =>
      ok({ items: path === '/api/deals' ? [DEAL] : [], next_cursor: null })) as never)
    await client.invalidateQueries()
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('holds the answer and stops reading once the space has data', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useSpaceHasData(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current).toBe(true))
    const reads = workReads().length

    await client.invalidateQueries()
    expect(result.current).toBe(true)
    expect(workReads()).toHaveLength(reads)
  })
})

/** REB-953: a switched-off module's reads are not made, and do not count as work. */
describe('with modules off', () => {
  function answerModules(off: Parameters<typeof spaceModules>[0]) {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/settings/modules'
        ? ok(spaceModules(off))
        : path === '/api/deals' || path === '/api/time-entries'
          ? // What a switched-off module's route answers: a failed read would count as work.
            Promise.resolve({
              error: { status: 404, code: 'module_off', detail: 'Il modulo Deal è spento' },
              response: new Response(null, { status: 404 }),
            })
          : ok({ items: [], next_cursor: null })) as never)
  }

  it('is an empty space when only the customers and documents are read and neither has a row', async () => {
    answerModules(['deals'])
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useSpaceEmpty(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.spaceEmpty).toBe(true))
    expect(result.current.workFailed).toBe(false)
    const paths = workReads().map(([path]) => path)
    expect(paths).not.toContain('/api/deals')
    expect(paths).not.toContain('/api/time-entries')
  })

  it('drops the two steps that lead to the deals', async () => {
    answerModules(['deals'])
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useFirstSteps(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.steps.map((step) => step.id)).toEqual(['tax', 'customer']))
  })

  it('keeps the four steps with every module on', async () => {
    answerModules([])
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/settings/modules' ? ok(spaceModules([])) : ok({ items: [], next_cursor: null })) as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useFirstSteps(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.steps.map((step) => step.id)).toEqual(['tax', 'customer', 'work', 'document'])
  })
})

/** REB-957: an administrator who answered «Decido dopo» finds the onboarding first. */
describe('with the onboarding skipped', () => {
  function answerOnboarding(onboarding: 'pending' | 'skipped' | 'done') {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/settings/modules'
        ? ok(spaceModules([], onboarding))
        : ok({ items: [], next_cursor: null })) as never)
  }

  it('lists the onboarding as the first step, undone and for the admin', async () => {
    answerOnboarding('skipped')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useFirstSteps(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.loading).toBe(false))

    const [first] = result.current.steps
    expect(first).toMatchObject({ id: 'onboarding', to: '/app/onboarding', canDo: true, done: false })
    expect(result.current.steps.map((step) => step.id).slice(1)).toEqual([
      'tax',
      'customer',
      'work',
      'document',
    ])
  })

  it.each(['pending', 'done'] as const)('does not list it while the onboarding is %s', async (state) => {
    answerOnboarding(state)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useFirstSteps(), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.steps.map((step) => step.id)).not.toContain('onboarding')
  })
})
