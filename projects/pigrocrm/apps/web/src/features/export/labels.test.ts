import { describe, expect, it } from 'vitest'
import {
  STATUS_TONES,
  andOthers,
  formatMebibytes,
  partCount,
  partsWord,
  quantity,
  readIndex,
} from './labels'
import { validateExportSearch } from './search'

describe('the status of an export', () => {
  it('draws each state in the tone the brief gives it', () => {
    expect(STATUS_TONES).toEqual({ building: 'gold', ready: 'ink', failed: 'danger', deleted: 'muted' })
  })
})

describe('the snapshot an export keeps', () => {
  const checklist = {
    parts: [
      { section: 'Lumen', key: 'sales', label: 'Registro', status: 'included', path: 'a.csv', count: 3, unit: 'rows' },
      { section: 'Lumen', key: 'sales', label: 'Registro', status: 'included', path: 'b.csv', count: 0, unit: 'rows' },
      { section: 'Lumen', key: 'files', label: 'File', status: 'omitted', cause: 'pack' },
      { section: 'Lumen', key: 'later', label: 'Poi', status: 'unavailable' },
      { status: 'nonsense' },
      'junk',
    ],
    missing: [{ section: 'Lumen', document: '2026/1', kind: 'XML', reason: 'il file non è archiviato' }, 7],
  }

  it('reads the lines the build writes and drops what is not one', () => {
    const index = readIndex(checklist)
    expect(index.lines.map((line) => [line.key, line.status, line.cause])).toEqual([
      ['sales', 'included', null],
      ['sales', 'included', null],
      ['files', 'omitted', 'pack'],
      ['later', 'unavailable', null],
    ])
    expect(index.missing).toEqual([
      { section: 'Lumen', document: '2026/1', kind: 'XML', reason: 'il file non è archiviato' },
    ])
  })

  it('counts a part once however many files it wrote', () => {
    expect(partCount(readIndex(checklist))).toBe(1)
    expect(partCount(readIndex({}))).toBe(0)
  })

  it('writes counts, sizes and the rest of the list the way the brief does', () => {
    expect(partsWord(1)).toBe('1 parte')
    expect(partsWord(10)).toBe('10 parti')
    expect(quantity(1, 'rows')).toBe('1 riga')
    expect(quantity(12, 'rows')).toBe('12 righe')
    expect(quantity(3, 'files')).toBe('3 file')
    expect(formatMebibytes(40_265_318)).toBe('38,4 MiB')
    expect(formatMebibytes(2_194)).toBe('2,1 KiB')
    expect(formatMebibytes(512)).toBe('512 B')
    expect(andOthers(5)).toBeNull()
    expect(andOthers(17)).toBe('e altre 12')
  })
})

describe('the URL of the page', () => {
  it('opens «Prepara» unless it is told another tab, and keeps an open export', () => {
    expect(validateExportSearch({})).toEqual({ tab: 'prepare' })
    expect(validateExportSearch({ tab: 'nonsense', export: '' })).toEqual({ tab: 'prepare' })
    expect(validateExportSearch({ tab: 'exports', export: 'e1' })).toEqual({ tab: 'exports', export: 'e1' })
  })
})
