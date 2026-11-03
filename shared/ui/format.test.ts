import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText, downloadBlob, formatMoney } from './format'

const NBSP = '\u00a0'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('formatMoney', () => {
  it('groups thousands from the first, as the Italian writes them', () => {
    expect(formatMoney('1500.00')).toBe(`1.500,00${NBSP}€`)
    expect(formatMoney('450')).toBe(`450,00${NBSP}€`)
  })

  it('keeps the cents of a decimal string a float would lose', () => {
    expect(formatMoney('0.29')).toBe(`0,29${NBSP}€`)
    expect(formatMoney('9007199254740993.01')).toContain('9.007.199.254.740.993,01')
  })
})

describe('downloadBlob', () => {
  it('clicks a link named for the file, then lets the URL go', () => {
    const create = vi.fn(() => 'blob:x')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const clicked: { href: string; download: string }[] = []
    const anchor = {
      href: '',
      download: '',
      click() {
        clicked.push({ href: this.href, download: this.download })
      },
      remove() {},
    }
    vi.stubGlobal('document', {
      createElement: () => anchor,
      body: { appendChild: () => {} },
    })
    downloadBlob(new Blob(['x']), 'cv.pdf')
    expect(clicked).toEqual([{ href: 'blob:x', download: 'cv.pdf' }])
    expect(revoke).toHaveBeenCalledWith('blob:x')
    vi.unstubAllGlobals()
  })
})

describe('copyText', () => {
  it('says whether the clipboard took it', async () => {
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('no'))
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    expect(await copyText('a')).toBe(true)
    expect(await copyText('b')).toBe(false)
    expect(writeText).toHaveBeenNthCalledWith(1, 'a')
    vi.unstubAllGlobals()
  })
})
