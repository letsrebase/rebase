import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDebounced } from './useDebounced'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('useDebounced', () => {
  it('starts on the value it is given', () => {
    const { result } = renderHook(() => useDebounced('rossi', 250))
    expect(result.current).toBe('rossi')
  })

  it('follows a changed value only once it has stopped changing for the delay', () => {
    const { result, rerender } = renderHook(({ value }) => useDebounced(value, 250), {
      initialProps: { value: 'r' },
    })

    rerender({ value: 'ro' })
    act(() => vi.advanceTimersByTime(200))
    rerender({ value: 'ros' })
    act(() => vi.advanceTimersByTime(200))
    expect(result.current).toBe('r')

    act(() => vi.advanceTimersByTime(50))
    expect(result.current).toBe('ros')
  })
})
