import { afterEach, describe, expect, it } from 'vitest'
import { rememberCliCode, takeCliCode } from './returnTo'

afterEach(() => window.localStorage.clear())

describe('the code kept across the sign-in', () => {
  it('is given back once', () => {
    rememberCliCode('ABCD-EFGH', 1000)
    expect(takeCliCode(2000)).toBe('ABCD-EFGH')
    expect(takeCliCode(2000)).toBeNull()
  })

  it('is forgotten after half an hour', () => {
    rememberCliCode('ABCD-EFGH', 0)
    expect(takeCliCode(31 * 60 * 1000)).toBeNull()
  })

  it('keeps a code as the server would read it, upper case', () => {
    rememberCliCode(' abcd-efgh ', 0)
    expect(takeCliCode(1)).toBe('ABCD-EFGH')
  })

  it('keeps nothing that is not a code of the server alphabet', () => {
    rememberCliCode('ABCD-EFG0', 0)
    expect(takeCliCode(1)).toBeNull()
    rememberCliCode('ABCD-EFGHJ', 0)
    expect(takeCliCode(1)).toBeNull()
    rememberCliCode('https://evil.example/x', 0)
    expect(takeCliCode(1)).toBeNull()
    window.localStorage.setItem('rebase.returnTo', '{"code":"//evil","at":1}')
    expect(takeCliCode(2)).toBeNull()
    window.localStorage.setItem('rebase.returnTo', 'not json')
    expect(takeCliCode(2)).toBeNull()
  })
})
