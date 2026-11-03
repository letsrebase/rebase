import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { useCursorPages } from './useCursorPages'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useCursorPages', () => {
  it('shows the first page, then appends one page per loadMore until the cursor runs out', async () => {
    const fetchPage = vi.fn(async (cursor: string | undefined) =>
      cursor === undefined
        ? { items: ['a', 'b'], next_cursor: 'c1' }
        : { items: ['c'], next_cursor: null },
    )
    const { result } = renderHook(() => useCursorPages({ queryKey: ['t', 'rows'], fetchPage }), {
      wrapper,
    })

    await waitFor(() => expect(result.current.items).toEqual(['a', 'b']))
    expect(result.current.hasMore).toBe(true)

    act(() => result.current.loadMore())

    await waitFor(() => expect(result.current.items).toEqual(['a', 'b', 'c']))
    expect(result.current.hasMore).toBe(false)
    expect(fetchPage.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, 'c1'])
  })

  it('starts a fresh walk at the first page when the key changes, keeping the old rows meanwhile', async () => {
    let release: (() => void) | undefined
    const fetchPage = vi.fn(async () => ({ items: ['x'], next_cursor: null }))
    const { result, rerender } = renderHook(
      ({ term }: { term: string }) =>
        useCursorPages({
          queryKey: ['t', { search: term }],
          fetchPage: () =>
            term === 'b'
              ? new Promise<{ items: string[]; next_cursor: null }>((resolve) => {
                  release = () => resolve({ items: ['y'], next_cursor: null })
                })
              : fetchPage(),
        }),
      { wrapper, initialProps: { term: 'a' } },
    )
    await waitFor(() => expect(result.current.items).toEqual(['x']))

    rerender({ term: 'b' })

    expect(result.current.items).toEqual(['x'])
    expect(result.current.isLoading).toBe(false)
    await waitFor(() => expect(release).toBeDefined())
    act(() => release?.())
    await waitFor(() => expect(result.current.items).toEqual(['y']))
  })
})
