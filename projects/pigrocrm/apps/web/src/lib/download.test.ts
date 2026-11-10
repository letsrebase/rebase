import { afterEach, describe, expect, it, vi } from 'vitest'
import { filenameFromDisposition, saveResponse } from './download'

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

describe('saveResponse', () => {
  afterEach(() => vi.restoreAllMocks())

  it('names the saved file after the server\'s Content-Disposition and revokes the URL afterwards', async () => {
    vi.useFakeTimers()
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    let saved: string | null = null
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved = this.download
    })

    await saveResponse(
      new Response('x', { headers: { 'Content-Disposition': 'attachment; filename="registro-2026.csv"' } }),
    )

    expect(saved).toBe('registro-2026.csv')
    // Not synchronously: a revoked URL can cancel the save in some browsers.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    vi.runAllTimers()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x')
    vi.useRealTimers()
  })
})
