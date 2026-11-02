import { describe, expect, it, vi } from 'vitest'
import { isChunkLoadError, reloadOncePerMinute } from './chunkReload'

function memoryStorage() {
  const map = new Map<string, string>()
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
  }
}

describe('isChunkLoadError', () => {
  it('recognises the browsers\' messages for a missing lazy chunk', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: /app/assets/x.js'))).toBe(true)
    expect(isChunkLoadError(new Error('error loading dynamically imported module'))).toBe(true)
    expect(isChunkLoadError(new Error('Importing a module script failed.'))).toBe(true)
  })

  it('leaves every other error alone', () => {
    expect(isChunkLoadError(new Error('boom'))).toBe(false)
  })
})

describe('reloadOncePerMinute', () => {
  it('reloads once, then refuses within the minute so a persistent failure cannot loop', () => {
    const storage = memoryStorage()
    const reload = vi.fn()
    expect(reloadOncePerMinute(storage, 1_000_000, reload)).toBe(true)
    expect(reloadOncePerMinute(storage, 1_030_000, reload)).toBe(false)
    expect(reloadOncePerMinute(storage, 1_061_000, reload)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(2)
  })
})
