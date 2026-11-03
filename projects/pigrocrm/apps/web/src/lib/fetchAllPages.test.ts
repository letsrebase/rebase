import { describe, expect, it, vi } from 'vitest'
import { fetchAllPages } from './fetchAllPages'

describe('fetchAllPages', () => {
  it('returns every row in order, handing each page the cursor the previous one issued', async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce({ items: [1, 2], next_cursor: 'c1' })
      .mockResolvedValueOnce({ items: [3], next_cursor: 'c2' })
      .mockResolvedValueOnce({ items: [4], next_cursor: null })

    const result = await fetchAllPages<number>(fetchPage)

    expect(result).toEqual({ items: [1, 2, 3, 4], truncated: false })
    expect(fetchPage.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, 'c1', 'c2'])
  })

  it('stops after one request when the first page has no cursor', async () => {
    const fetchPage = vi.fn().mockResolvedValue({ items: ['a'] })

    expect(await fetchAllPages<string>(fetchPage)).toEqual({ items: ['a'], truncated: false })
    expect(fetchPage).toHaveBeenCalledTimes(1)
  })

  it('stops at maxPages and reports the list as truncated while the server still has more', async () => {
    const fetchPage = vi.fn().mockResolvedValue({ items: [0], next_cursor: 'more' })

    const result = await fetchAllPages<number>(fetchPage, { maxPages: 3 })

    expect(result).toEqual({ items: [0, 0, 0], truncated: true })
    expect(fetchPage).toHaveBeenCalledTimes(3)
  })

  it('is not truncated when the last allowed page is also the last one', async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce({ items: [1], next_cursor: 'c1' })
      .mockResolvedValueOnce({ items: [2], next_cursor: null })

    expect((await fetchAllPages<number>(fetchPage, { maxPages: 2 })).truncated).toBe(false)
  })

  it('lets a failed page reject the whole walk', async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce({ items: [1], next_cursor: 'c1' })
      .mockRejectedValueOnce(new Error('boom'))

    await expect(fetchAllPages<number>(fetchPage)).rejects.toThrow('boom')
  })
})
