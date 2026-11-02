import { describe, expect, it } from 'vitest'
import { filenameFromDisposition } from './download'

describe('filenameFromDisposition', () => {
  it('reads a quoted filename', () => {
    expect(filenameFromDisposition('attachment; filename="fascicolo-2026.zip"')).toBe(
      'fascicolo-2026.zip',
    )
  })

  it('prefers the RFC 5987 form and decodes it', () => {
    expect(
      filenameFromDisposition(`attachment; filename="x.csv"; filename*=UTF-8''registro%20%C3%A8.csv`),
    ).toBe('registro è.csv')
  })

  it('answers an empty name when the header is absent', () => {
    expect(filenameFromDisposition(null)).toBe('')
  })
})
