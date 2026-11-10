import { describe, expect, it } from 'vitest'
import { importSentence } from './labels'

describe('importSentence', () => {
  it('adds the proposals, with the singular where the count is one', () => {
    expect(importSentence(41, 2, 3)).toBe('41 movimenti, 2 già presenti, 3 con una proposta')
    expect(importSentence(1, 1, 1)).toBe('1 movimento, 1 già presente, 1 con una proposta')
  })

  it('leaves the proposals out when there are none', () => {
    expect(importSentence(41, 2, 0)).toBe('41 movimenti, 2 già presenti')
  })
})
