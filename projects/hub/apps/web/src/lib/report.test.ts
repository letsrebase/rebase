import { describe, expect, it } from 'vitest'
import type { MatchReport, ReportDay } from './api'
import {
  TUTTO,
  invoiceLabel,
  isoWeek,
  monthsBetween,
  perMonth,
  perWeek,
  periodInvoices,
  periodMonths,
  periodParam,
  progressLine,
  romeToday,
  sliceDays,
  sumHours,
} from './report'

const day = (data: string, ore: string, fatture: string[] = [], descrizioni: string[] = []): ReportDay => ({
  data,
  ore,
  descrizioni,
  fatture,
})

/** A recorded engagement from 28 September 2026: twelve days of eight hours, invoice
 *  12/2026 across September and October, a proforma in October, November not billed. */
const REPORT: MatchReport = {
  match_id: 'm1',
  pigro_url: 'https://pigro.letsrebase.com/ada/app/deal/d1',
  pigro_stato: 'collegato',
  giorni_previsti: 40,
  ore_previste: '320.00',
  totale_ore: '96.00',
  giorni_equivalenti: '12.00',
  avanzamento: '30.00',
  ore_fatturate: '32.00',
  ore_non_fatturate: '64.00',
  per_giorno: [
    day('2026-09-29', '8.00', ['12/2026']),
    day('2026-09-30', '8.00', ['12/2026']),
    day('2026-10-01', '8.00', ['12/2026']),
    day('2026-10-02', '8.00', ['12/2026']),
    day('2026-10-05', '8.00', ['proforma 4/2026']),
    day('2026-10-06', '8.00', ['proforma 4/2026']),
    day('2026-10-26', '8.00'),
    day('2026-10-27', '8.00'),
    day('2026-11-02', '8.00'),
    day('2026-11-03', '8.00'),
    day('2026-11-04', '8.00'),
    day('2026-11-05', '8.00'),
  ],
  per_settimana: [],
  per_mese: [],
  fatture: [
    {
      numero: '4/2026',
      tipo: 'proforma',
      data: '2026-10-31',
      stato: 'confermata',
      stato_pagamento: 'da_incassare',
      ore: '16.00',
    },
    {
      numero: '12/2026',
      tipo: 'fattura',
      data: '2026-10-02',
      stato: 'emessa',
      stato_pagamento: 'incassato',
      ore: '32.00',
    },
  ],
}

describe('the period in the URL (REB-503)', () => {
  it('keeps a month as YYYY-MM and «tutto», and drops anything else', () => {
    expect(periodParam('2026-10')).toBe('2026-10')
    expect(periodParam('tutto')).toBe(TUTTO)
    expect(periodParam('2026-13')).toBeUndefined()
    expect(periodParam('2026-1')).toBeUndefined()
    expect(periodParam('ottobre')).toBeUndefined()
    expect(periodParam('')).toBeUndefined()
    // The router's `parseSearch` hands a bare number over as a number.
    expect(periodParam(202610)).toBeUndefined()
    expect(periodParam(undefined)).toBeUndefined()
  })
})

describe('the months the page offers', () => {
  it('runs from the letter’s start to today, newest first', () => {
    expect(monthsBetween('2026-09-28', '2026-11-15')).toEqual(['2026-09', '2026-10', '2026-11'])
    expect(monthsBetween('2026-11-28', '2027-02-01')).toEqual(['2026-11', '2026-12', '2027-01', '2027-02'])
    expect(periodMonths('2026-09-28', '2026-11-15', REPORT.per_giorno)).toEqual(['2026-11', '2026-10', '2026-09'])
  })

  it('offers the current month alone for a letter that starts later, and the month a link names', () => {
    expect(periodMonths('2027-01-10', '2026-11-15', [])).toEqual(['2026-11'])
    expect(periodMonths(null, '2026-11-15', [])).toEqual(['2026-11'])
    // No letter start known: the hours' own months are there.
    expect(periodMonths(null, '2026-11-15', REPORT.per_giorno)).toEqual(['2026-11', '2026-10', '2026-09'])
    expect(periodMonths('2026-09-28', '2026-11-15', [], '2025-03')).toEqual([
      '2026-11',
      '2026-10',
      '2026-09',
      '2025-03',
    ])
    expect(periodMonths('2026-09-28', '2026-11-15', [], TUTTO)).toEqual(['2026-11', '2026-10', '2026-09'])
  })

  it('reads today in Rome, where the hub and the CRM keep their own days', () => {
    // 23:30 UTC on 31 October is already 1 November in Rome (UTC+1 after the clocks change).
    expect(romeToday(new Date('2026-10-31T23:30:00Z'))).toBe('2026-11-01')
    expect(romeToday(new Date('2026-07-15T10:00:00Z'))).toBe('2026-07-15')
  })
})

describe('the days of a period', () => {
  it('slices a month out of the whole engagement, and keeps every day for «tutto»', () => {
    expect(sliceDays(REPORT.per_giorno, '2026-09').map((d) => d.data)).toEqual(['2026-09-29', '2026-09-30'])
    expect(sliceDays(REPORT.per_giorno, '2026-11')).toHaveLength(4)
    expect(sliceDays(REPORT.per_giorno, TUTTO)).toHaveLength(12)
    expect(sliceDays(REPORT.per_giorno, '2026-12')).toEqual([])
  })
})

describe('the sums', () => {
  it('adds the API’s decimal strings exactly, never as floats', () => {
    expect(sumHours(['0.10', '0.20'])).toBe('0.30')
    expect(sumHours(['7.50', '8.00', '0.25'])).toBe('15.75')
    expect(sumHours(['8'])).toBe('8.00')
    expect(sumHours([])).toBe('0.00')
  })

  it('groups a period by ISO week, Monday to Sunday, only with the period’s own days', () => {
    expect(perWeek(REPORT.per_giorno)).toEqual([
      { settimana: '2026-W40', da: '2026-09-28', a: '2026-10-04', ore: '32.00' },
      { settimana: '2026-W41', da: '2026-10-05', a: '2026-10-11', ore: '16.00' },
      { settimana: '2026-W44', da: '2026-10-26', a: '2026-11-01', ore: '16.00' },
      { settimana: '2026-W45', da: '2026-11-02', a: '2026-11-08', ore: '32.00' },
    ])
    // October alone: week 40 keeps only its October days.
    expect(perWeek(sliceDays(REPORT.per_giorno, '2026-10'))[0]).toEqual({
      settimana: '2026-W40',
      da: '2026-09-28',
      a: '2026-10-04',
      ore: '16.00',
    })
  })

  it('numbers the weeks across a year end as the server does', () => {
    expect(isoWeek('2026-12-31')).toEqual({ settimana: '2026-W53', da: '2026-12-28', a: '2027-01-03' })
    expect(isoWeek('2027-01-01').settimana).toBe('2026-W53')
    expect(isoWeek('2027-01-04').settimana).toBe('2027-W01')
    expect(
      perWeek([day('2026-12-31', '4.00'), day('2027-01-01', '3.50'), day('2027-01-04', '8.00')]),
    ).toEqual([
      { settimana: '2026-W53', da: '2026-12-28', a: '2027-01-03', ore: '7.50' },
      { settimana: '2027-W01', da: '2027-01-04', a: '2027-01-10', ore: '8.00' },
    ])
  })

  it('groups a period by month', () => {
    expect(perMonth(REPORT.per_giorno)).toEqual([
      { mese: '2026-09', ore: '16.00' },
      { mese: '2026-10', ore: '48.00' },
      { mese: '2026-11', ore: '32.00' },
    ])
    expect(perMonth(sliceDays(REPORT.per_giorno, '2026-10'))).toEqual([{ mese: '2026-10', ore: '48.00' }])
  })

  it('answers nothing for an empty report', () => {
    expect(perWeek([])).toEqual([])
    expect(perMonth([])).toEqual([])
    expect(periodInvoices({ per_giorno: [], fatture: [] }, '2026-11')).toEqual([])
    expect(periodInvoices({ per_giorno: [], fatture: [] }, TUTTO)).toEqual([])
  })
})

describe('the invoices of a period', () => {
  it('names an invoice as a day’s column does: a proforma says so', () => {
    expect(invoiceLabel(REPORT.fatture[1]!)).toBe('12/2026')
    expect(invoiceLabel(REPORT.fatture[0]!)).toBe('proforma 4/2026')
  })

  it('shows a two-month invoice in both months, each with that month’s hours', () => {
    const september = periodInvoices(REPORT, '2026-09')
    expect(september.map((i) => [invoiceLabel(i), i.ore])).toEqual([['12/2026', '16.00']])
    const october = periodInvoices(REPORT, '2026-10')
    expect(october.map((i) => [invoiceLabel(i), i.ore])).toEqual([
      ['proforma 4/2026', '16.00'],
      ['12/2026', '16.00'],
    ])
    // The rest of each row is the invoice's own.
    expect(october[1]).toMatchObject({ data: '2026-10-02', stato: 'emessa', stato_pagamento: 'incassato' })
  })

  it('shows no invoice for a month whose days none sits on', () => {
    expect(periodInvoices(REPORT, '2026-11')).toEqual([])
  })

  it('shows every invoice with all its hours for the whole engagement', () => {
    expect(periodInvoices(REPORT, TUTTO)).toEqual(REPORT.fatture)
  })

  it('keeps the CRM’s own hours for an invoice wholly inside the month, even on a day it shares', () => {
    // 3 November: 3 hours on 13/2026 and 5 on 14/2026, a day's total the page cannot split.
    const shared: Pick<MatchReport, 'per_giorno' | 'fatture'> = {
      per_giorno: [day('2026-11-02', '8.00', ['13/2026']), day('2026-11-03', '8.00', ['13/2026', '14/2026'])],
      fatture: [
        { numero: '14/2026', tipo: 'fattura', data: null, stato: 'bozza', stato_pagamento: 'da_incassare', ore: '5.00' },
        { numero: '13/2026', tipo: 'fattura', data: '2026-11-30', stato: 'emessa', stato_pagamento: 'da_incassare', ore: '11.00' },
      ],
    }
    expect(periodInvoices(shared, '2026-11').map((i) => i.ore)).toEqual(['5.00', '11.00'])
  })
})

describe('the progress line', () => {
  it('reads the total against the letter’s days', () => {
    expect(progressLine(REPORT)).toBe('96 ore, 12 giorni su 40 previsti (30%)')
    expect(
      progressLine({ totale_ore: '7.50', giorni_equivalenti: '0.94', giorni_previsti: 1, avanzamento: '93.75' }),
    ).toBe('7,5 ore, 0,94 giorni su 1 previsto (93,75%)')
  })

  it('says the hours and the days alone without an estimate', () => {
    expect(progressLine({ ...REPORT, giorni_previsti: null, avanzamento: null })).toBe(
      '96 ore, 12 giorni',
    )
    expect(progressLine({ totale_ore: '8.00', giorni_equivalenti: '1.00', giorni_previsti: null, avanzamento: null })).toBe(
      '8 ore, 1 giorno',
    )
    expect(progressLine({ totale_ore: '1.00', giorni_equivalenti: '0.13', giorni_previsti: null, avanzamento: null })).toBe(
      '1 ora, 0,13 giorni',
    )
  })

  it('says nothing is done yet on an empty report', () => {
    expect(
      progressLine({ totale_ore: '0.00', giorni_equivalenti: '0.00', giorni_previsti: 40, avanzamento: '0.00' }),
    ).toBe('0 ore, 0 giorni su 40 previsti (0%)')
  })
})
