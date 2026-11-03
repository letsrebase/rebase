import { describe, expect, it } from 'vitest'
import {
  parseHours,
  parseRate,
  parseValue,
  simulatorRequest,
  yearOfIso,
  type SimulatorFields,
} from './simulatorInput'

const BLANK: SimulatorFields = { source: 'amount', value: '', hours: '', rate: '', deal: null }

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
    expect(simulatorRequest({ ...BLANK, value: '20.000,00' })).toEqual({
      ok: true,
      input: { budgeted_value: '20000.00' },
    })
  })

  it('sends hours with a rate when there is no amount', () => {
    expect(simulatorRequest({ ...BLANK, hours: '100', rate: '80,5' })).toEqual({
      ok: true,
      input: { budgeted_hours: '100', hourly_rate: '80.5' },
    })
  })

  it('lets the amount win over hours and rate, as the server does', () => {
    expect(simulatorRequest({ ...BLANK, value: '500', hours: '10', rate: '50' })).toEqual({
      ok: true,
      input: { budgeted_value: '500' },
    })
  })

  it('says what is missing rather than sending nothing', () => {
    expect(simulatorRequest(BLANK)).toEqual({
      ok: false,
      errors: { form: 'Scrivi un importo, oppure le ore e la tariffa oraria.' },
    })
    expect(simulatorRequest({ ...BLANK, hours: '100' })).toMatchObject({ ok: false })
  })

  it('names the field that is wrong, and only that one', () => {
    const request = simulatorRequest({ ...BLANK, value: 'tanto', hours: '10' })
    expect(request).toEqual({
      ok: false,
      errors: { value: 'Importo: scrivi un numero, per esempio 12.000,50' },
    })
  })

  it('asks for a deal that has value lines by its id, and sends no estimate beside it', () => {
    const deal = {
      id: 'd1',
      expected_value: '12000.00',
      budgeted_value: '9000.00',
      budgeted_hours: null,
      hourly_rate: null,
    }
    expect(simulatorRequest({ ...BLANK, source: 'deal', deal, date: '2026-12-31' })).toEqual({
      ok: true,
      input: { deal_id: 'd1', expected_date: '2026-12-31' },
    })
  })

  it('takes the own estimate of a deal with no lines: its value, else its hours at its rate', () => {
    const none = { id: 'd2', expected_value: null }
    expect(
      simulatorRequest({
        ...BLANK,
        source: 'deal',
        deal: { ...none, budgeted_value: '9000.00', budgeted_hours: null, hourly_rate: null },
      }),
    ).toEqual({ ok: true, input: { budgeted_value: '9000.00' } })
    expect(
      simulatorRequest({
        ...BLANK,
        source: 'deal',
        deal: { ...none, budgeted_value: null, budgeted_hours: '40.00', hourly_rate: '75.000000' },
      }),
    ).toEqual({ ok: true, input: { budgeted_hours: '40.00', hourly_rate: '75.000000' } })
  })

  it('refuses a deal with no value at all, and no deal at all', () => {
    expect(simulatorRequest({ ...BLANK, source: 'deal' })).toMatchObject({
      ok: false,
      errors: { deal: 'Scegli un deal.' },
    })
    const empty = {
      id: 'd3',
      expected_value: null,
      budgeted_value: null,
      budgeted_hours: '40.00',
      hourly_rate: null,
    }
    expect(simulatorRequest({ ...BLANK, source: 'deal', deal: empty })).toMatchObject({
      ok: false,
      errors: { deal: expect.stringContaining('non ha un valore') },
    })
  })

  it('carries the day the money arrives, and the contract chosen, beside the estimate', () => {
    expect(
      simulatorRequest({ ...BLANK, value: '20000', date: '2027-03-15', contractId: 'c1' }),
    ).toEqual({
      ok: true,
      input: { budgeted_value: '20000', expected_date: '2027-03-15', contract_id: 'c1' },
    })
    expect(simulatorRequest({ ...BLANK, value: '20000', date: '2027-03-15', contractId: null })).toEqual({
      ok: true,
      input: { budgeted_value: '20000', expected_date: '2027-03-15' },
    })
  })

  it('still refuses an estimate it cannot read, whatever contract is chosen', () => {
    expect(simulatorRequest({ ...BLANK, date: '2027-03-15', contractId: 'c1' })).toMatchObject({
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
