import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { useWaitingCount } from './queries'
import { item, ok } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
vi.mock('@/lib/auth', () => ({ useIsAdmin: () => true }))

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => vi.mocked(api.GET).mockReset())

describe('the count beside «In arrivo»', () => {
  it('is what waits for a person: the totals of the new items and the proposed ones, nothing else', async () => {
    vi.mocked(api.GET).mockImplementation(((_path: string, init?: { params?: { query?: { state?: string } } }) =>
      Promise.resolve(
        ok({ items: [item()], next_cursor: null, total: init?.params?.query?.state === 'new' ? 2 : 1 }),
      )) as never)
    const { result } = renderHook(() => useWaitingCount(), { wrapper })
    await waitFor(() => expect(result.current).toBe(3))
    const states = vi.mocked(api.GET).mock.calls.map((call) => {
      const [, init] = call as unknown as [string, { params: { query: { state: string } } }]
      return init.params.query.state
    })
    expect(states.sort()).toEqual(['discarded', 'duplicate', 'filed', 'new', 'proposed'])
  })

  it('is zero, and draws nothing, while it is not known or when the read failed', async () => {
    vi.mocked(api.GET).mockResolvedValue({ error: { detail: 'x', code: 'internal', title: 'x', status: 500, instance: '/' }, response: new Response(null, { status: 500 }) } as never)
    const { result } = renderHook(() => useWaitingCount(), { wrapper })
    expect(result.current).toBe(0)
    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    expect(result.current).toBe(0)
  })
})
