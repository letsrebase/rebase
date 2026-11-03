import { describe, expect, it } from 'vitest'
import { keepWhileTyping } from './keepWhileTyping'

const previous = (key: unknown[]) => ({ queryKey: key })

describe('keepWhileTyping', () => {
  it('keeps the previous rows when only the search term changed', () => {
    const keep = keepWhileTyping<string[]>(['deals', { search: 'ro', azienda_id: 'a1' }])
    expect(keep(['x'], previous(['deals', { search: 'r', azienda_id: 'a1' }]))).toEqual(['x'])
  })

  it('drops them when any other part of the key changed', () => {
    const keep = keepWhileTyping<string[]>(['deals', { search: 'ro', azienda_id: 'a2' }])
    expect(keep(['x'], previous(['deals', { search: 'ro', azienda_id: 'a1' }]))).toBeUndefined()
  })

  it('has nothing to keep without a previous query', () => {
    expect(keepWhileTyping<string[]>(['deals', {}])(undefined, undefined)).toBeUndefined()
  })
})
