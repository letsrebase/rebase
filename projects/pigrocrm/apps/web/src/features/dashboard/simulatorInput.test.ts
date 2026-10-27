import { describe, expect, it } from 'vitest'
import {
  parseHours,
  parseRate,
  parseValue,
  simulatorRequest,
  yearOfIso,
  type SimulatorFields,
} from './simulatorInput'

const BLANK: SimulatorFields = { source: 'importo', valore: '', ore: '', tariffa: '', deal: null }

describe('the amounts an Italian keyboard produces', () => {
  it.each([
    ['12.000,50', '12000.50'],
    ['12000,5', '12000.5'],
    ['1.234.567,89', '1234567.89'],
    ['12000.50', '12000.50'],
    [' 80 ', '80'],
    ['0,5', '0.5'],
  ])('reads %s as the decimal %s, as a string and never as a number', (typed, sent) => {
    expect(parseValue(typed)).toEqual({ ok: true, value: sent })
  })

  it('keeps a blank field blank, which is not an error and not a zero', () => {
    expect(parseValue('')).toBeNull()
    expect(parseValue('   ')).toBeNull()
  })

  it.each(['abc', '12,3,4', '-5', '1e3', '12 euro'])('refuses %s with a sentence', (typed) => {
    expect(parseValue(typed)).toMatchObject({ ok: false })
  })

  it('refuses more decimals than the column holds, and a value too large for it', () => {
    expect(parseValue('10,005')).toEqual({ ok: false, error: 'Importo: al massimo 2 decimali' })
    expect(parseValue('12345678901')).toEqual({ ok: false, error: 'Importo: troppo grande' })
    expect(parseHours('1,234')).toMatchObject({ ok: false })
    expect(parseRate('80,123456')).toEqual({ ok: true, value: '80.123456' })
    expect(parseRate('80,1234567')).toMatchObject({ ok: false })
  })
})

describe('the request the fields make', () => {
  it('sends a typed amount as the deal value', () => {
    expect(simulatorRequest({ ...BLANK, valore: '20.000,00' })).toEqual({
      ok: true,
      input: { valore_preventivato: '20000.00' },
    })
  })

  it('sends hours with a rate when there is no amount', () => {
    expect(simulatorRequest({ ...BLANK, ore: '100', tariffa: '80,5' })).toEqual({
      ok: true,
      input: { ore_preventivate: '100', tariffa_oraria: '80.5' },
    })
  })

  it('lets the amount win over hours and rate, as the server does', () => {
    expect(simulatorRequest({ ...BLANK, valore: '500', ore: '10', tariffa: '50' })).toEqual({
      ok: true,
      input: { valore_preventivato: '500' },
    })
  })

  it('says what is missing rather than sending nothing', () => {
    expect(simulatorRequest(BLANK)).toEqual({
      ok: false,
      errors: { form: 'Scrivi un importo, oppure le ore e la tariffa oraria.' },
    })
    expect(simulatorRequest({ ...BLANK, ore: '100' })).toMatchObject({ ok: false })
  })

  it('names the field that is wrong, and only that one', () => {
    const request = simulatorRequest({ ...BLANK, valore: 'tanto', ore: '10' })
    expect(request).toEqual({
      ok: false,
      errors: { valore: 'Importo: scrivi un numero, per esempio 12.000,50' },
    })
  })

  it('takes a deal\'s own estimate: its value, else its hours at its rate', () => {
    const deal = { valore_preventivato: '9000.00', ore_preventivate: null, tariffa_oraria: null }
    expect(simulatorRequest({ ...BLANK, source: 'deal', deal })).toEqual({
      ok: true,
      input: { valore_preventivato: '9000.00' },
    })
    expect(
      simulatorRequest({
        ...BLANK,
        source: 'deal',
        deal: { valore_preventivato: null, ore_preventivate: '40.00', tariffa_oraria: '75.000000' },
      }),
    ).toEqual({ ok: true, input: { ore_preventivate: '40.00', tariffa_oraria: '75.000000' } })
  })

  it('refuses a deal with no estimate, and no deal at all', () => {
    expect(simulatorRequest({ ...BLANK, source: 'deal' })).toMatchObject({
      ok: false,
      errors: { deal: 'Scegli un deal.' },
    })
    const empty = { valore_preventivato: null, ore_preventivate: '40.00', tariffa_oraria: null }
    expect(simulatorRequest({ ...BLANK, source: 'deal', deal: empty })).toMatchObject({
      ok: false,
      errors: { deal: expect.stringContaining('non ha una stima') },
    })
  })

  it('carries the day the money arrives, and the contract chosen, beside the estimate', () => {
    expect(
      simulatorRequest({ ...BLANK, valore: '20000', data: '2027-03-15', contractId: 'c1' }),
    ).toEqual({
      ok: true,
      input: { valore_preventivato: '20000', data_prevista: '2027-03-15', contract_id: 'c1' },
    })
    expect(simulatorRequest({ ...BLANK, valore: '20000', data: '2027-03-15', contractId: null })).toEqual({
      ok: true,
      input: { valore_preventivato: '20000', data_prevista: '2027-03-15' },
    })
  })

  it('still refuses an estimate it cannot read, whatever contract is chosen', () => {
    expect(simulatorRequest({ ...BLANK, data: '2027-03-15', contractId: 'c1' })).toMatchObject({
      ok: false,
    })
  })
})

describe('the year of the date', () => {
  it('is the calendar year written in it, whatever the time zone', () => {
    expect(yearOfIso('2026-12-31')).toBe(2026)
    expect(yearOfIso('2027-01-01')).toBe(2027)
  })

  it('is nothing for what is not a date, or a year the API does not answer for', () => {
    expect(yearOfIso('')).toBeNull()
    expect(yearOfIso('31/12/2026')).toBeNull()
    expect(yearOfIso('1999-06-01')).toBeNull()
    expect(yearOfIso('2300-06-01')).toBeNull()
  })
})
