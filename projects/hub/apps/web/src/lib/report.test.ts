import { describe, expect, it } from 'vitest'
import type { MatchReport, ReportDay, ReportDayInvoice } from './api'
import {
  ALL,
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

/** A day's hours on one invoice, a `fattura` unless `kind` says otherwise. */
const on = (number: string, hours: string, kind = 'invoice'): ReportDayInvoice => ({ number, kind, hours })

/** A day as the hub sends it: `invoices` names the invoices of `shares`, in their order. */
const day = (date: string, hours: string, shares: ReportDayInvoice[] = [], descriptions: string[] = []): ReportDay => ({
  date,
  hours,
  descriptions,
  invoices: shares.map(invoiceLabel),
  hours_by_invoice: shares,
})

/** A recorded engagement from 28 September 2026: twelve days of eight hours, invoice
 *  12/2026 across September and October, a proforma in October, November not billed. */
const REPORT: MatchReport = {
  match_id: 'm1',
  pigro_url: 'https://pigro.letsrebase.com/ada/app/deal/d1',
  pigro_status: 'linked',
  expected_days: 40,
  expected_hours: '320.00',
  hours_total: '96.00',
  equivalent_days: '12.00',
  progress: '30.00',
  invoiced_hours: '32.00',
  hours_uninvoiced: '64.00',
  by_day: [
    day('2026-09-29', '8.00', [on('12/2026', '8.00')]),
    day('2026-09-30', '8.00', [on('12/2026', '8.00')]),
    day('2026-10-01', '8.00', [on('12/2026', '8.00')]),
    day('2026-10-02', '8.00', [on('12/2026', '8.00')]),
    day('2026-10-05', '8.00', [on('4/2026', '8.00', 'proforma')]),
    day('2026-10-06', '8.00', [on('4/2026', '8.00', 'proforma')]),
    day('2026-10-26', '8.00'),
    day('2026-10-27', '8.00'),
    day('2026-11-02', '8.00'),
    day('2026-11-03', '8.00'),
    day('2026-11-04', '8.00'),
    day('2026-11-05', '8.00'),
  ],
  by_week: [],
  by_month: [],
  invoices: [
    {
      number: '4/2026',
      kind: 'proforma',
      date: '2026-10-31',
      status: 'confirmed',
      payment_status: 'from_unpaid',
      hours: '16.00',
    },
    {
      number: '12/2026',
      kind: 'invoice',
      date: '2026-10-02',
      status: 'issued',
      payment_status: 'paid',
      hours: '32.00',
    },
  ],
}

describe('the period in the URL (REB-503)', () => {
  it('keeps a month as YYYY-MM and «tutto», and drops anything else', () => {
    expect(periodParam('2026-10')).toBe('2026-10')
    expect(periodParam('all')).toBe(ALL)
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
    expect(periodMonths('2026-09-28', '2026-11-15', REPORT.by_day)).toEqual(['2026-11', '2026-10', '2026-09'])
  })

  it('offers the current month alone for a letter that starts later, and the month a link names', () => {
    expect(periodMonths('2027-01-10', '2026-11-15', [])).toEqual(['2026-11'])
    expect(periodMonths(null, '2026-11-15', [])).toEqual(['2026-11'])
    // No letter start known: the hours' own months are there.
    expect(periodMonths(null, '2026-11-15', REPORT.by_day)).toEqual(['2026-11', '2026-10', '2026-09'])
    expect(periodMonths('2026-09-28', '2026-11-15', [], '2025-03')).toEqual([
      '2026-11',
      '2026-10',
      '2026-09',
      '2025-03',
    ])
    expect(periodMonths('2026-09-28', '2026-11-15', [], ALL)).toEqual(['2026-11', '2026-10', '2026-09'])
  })

  it('reads today in Rome, where the hub and the CRM keep their own days', () => {
    // 23:30 UTC on 31 October is already 1 November in Rome (UTC+1 after the clocks change).
    expect(romeToday(new Date('2026-10-31T23:30:00Z'))).toBe('2026-11-01')
    expect(romeToday(new Date('2026-07-15T10:00:00Z'))).toBe('2026-07-15')
  })
})

describe('the days of a period', () => {
  it('slices a month out of the whole engagement, and keeps every day for «tutto»', () => {
    expect(sliceDays(REPORT.by_day, '2026-09').map((d) => d.date)).toEqual(['2026-09-29', '2026-09-30'])
    expect(sliceDays(REPORT.by_day, '2026-11')).toHaveLength(4)
    expect(sliceDays(REPORT.by_day, ALL)).toHaveLength(12)
    expect(sliceDays(REPORT.by_day, '2026-12')).toEqual([])
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
    expect(perWeek(REPORT.by_day)).toEqual([
      { week: '2026-W40', from_date: '2026-09-28', to_date: '2026-10-04', hours: '32.00' },
      { week: '2026-W41', from_date: '2026-10-05', to_date: '2026-10-11', hours: '16.00' },
      { week: '2026-W44', from_date: '2026-10-26', to_date: '2026-11-01', hours: '16.00' },
      { week: '2026-W45', from_date: '2026-11-02', to_date: '2026-11-08', hours: '32.00' },
    ])
    // October alone: week 40 keeps only its October days.
    expect(perWeek(sliceDays(REPORT.by_day, '2026-10'))[0]).toEqual({
      week: '2026-W40',
      from_date: '2026-09-28',
      to_date: '2026-10-04',
      hours: '16.00',
    })
  })

  it('numbers the weeks across a year end as the server does', () => {
    expect(isoWeek('2026-12-31')).toEqual({ week: '2026-W53', from_date: '2026-12-28', to_date: '2027-01-03' })
    expect(isoWeek('2027-01-01').week).toBe('2026-W53')
    expect(isoWeek('2027-01-04').week).toBe('2027-W01')
    expect(
      perWeek([day('2026-12-31', '4.00'), day('2027-01-01', '3.50'), day('2027-01-04', '8.00')]),
    ).toEqual([
      { week: '2026-W53', from_date: '2026-12-28', to_date: '2027-01-03', hours: '7.50' },
      { week: '2027-W01', from_date: '2027-01-04', to_date: '2027-01-10', hours: '8.00' },
    ])
  })

  it('groups a period by month', () => {
    expect(perMonth(REPORT.by_day)).toEqual([
      { month: '2026-09', hours: '16.00' },
      { month: '2026-10', hours: '48.00' },
      { month: '2026-11', hours: '32.00' },
    ])
    expect(perMonth(sliceDays(REPORT.by_day, '2026-10'))).toEqual([{ month: '2026-10', hours: '48.00' }])
  })

  it('answers nothing for an empty report', () => {
    expect(perWeek([])).toEqual([])
    expect(perMonth([])).toEqual([])
    expect(periodInvoices({ by_day: [], invoices: [] }, '2026-11')).toEqual([])
    expect(periodInvoices({ by_day: [], invoices: [] }, ALL)).toEqual([])
  })
})

describe('the invoices of a period', () => {
  it('names an invoice as a day’s column does: a proforma says so', () => {
    expect(invoiceLabel(REPORT.invoices[1]!)).toBe('12/2026')
    expect(invoiceLabel(REPORT.invoices[0]!)).toBe('proforma 4/2026')
  })

  it('shows a two-month invoice in both months, each with that month’s hours', () => {
    const september = periodInvoices(REPORT, '2026-09')
    expect(september.map((i) => [invoiceLabel(i), i.hours])).toEqual([['12/2026', '16.00']])
    const october = periodInvoices(REPORT, '2026-10')
    expect(october.map((i) => [invoiceLabel(i), i.hours])).toEqual([
      ['proforma 4/2026', '16.00'],
      ['12/2026', '16.00'],
    ])
    // The rest of each row is the invoice's own.
    expect(october[1]).toMatchObject({ date: '2026-10-02', status: 'issued', payment_status: 'paid' })
  })

  it('shows no invoice for a month whose days none sits on', () => {
    expect(periodInvoices(REPORT, '2026-11')).toEqual([])
  })

  it('shows every invoice with all its hours for the whole engagement', () => {
    expect(periodInvoices(REPORT, ALL)).toEqual(REPORT.invoices)
  })

  it('gives a two-month invoice only its own hours of a day partly unbilled (REB-505)', () => {
    // 2 November: 8 hours, 3 of them on 14/2026 and 5 not billed yet.
    const partly: Pick<MatchReport, 'by_day' | 'invoices'> = {
      by_day: [day('2026-10-30', '8.00', [on('14/2026', '8.00')]), day('2026-11-02', '8.00', [on('14/2026', '3.00')])],
      invoices: [
        { number: '14/2026', kind: 'invoice', date: null, status: 'draft', payment_status: 'from_unpaid', hours: '11.00' },
      ],
    }
    expect(periodInvoices(partly, '2026-11').map((i) => [invoiceLabel(i), i.hours])).toEqual([['14/2026', '3.00']])
    expect(periodInvoices(partly, '2026-10').map((i) => [invoiceLabel(i), i.hours])).toEqual([['14/2026', '8.00']])
    expect(periodInvoices(partly, ALL).map((i) => i.hours)).toEqual(['11.00'])
  })

  it('splits a day on two invoices between them, each with its own hours (REB-505)', () => {
    // 3 November: 3 hours on 13/2026 and 5 on 14/2026, each invoice running into another month.
    const split: Pick<MatchReport, 'by_day' | 'invoices'> = {
      by_day: [
        day('2026-10-30', '8.00', [on('13/2026', '8.00')]),
        day('2026-11-03', '8.00', [on('13/2026', '3.00'), on('14/2026', '5.00')]),
        day('2026-12-01', '8.00', [on('14/2026', '8.00')]),
      ],
      invoices: [
        { number: '14/2026', kind: 'invoice', date: null, status: 'draft', payment_status: 'from_unpaid', hours: '13.00' },
        { number: '13/2026', kind: 'invoice', date: '2026-11-30', status: 'issued', payment_status: 'from_unpaid', hours: '11.00' },
      ],
    }
    expect(periodInvoices(split, '2026-11').map((i) => [invoiceLabel(i), i.hours])).toEqual([
      ['14/2026', '5.00'],
      ['13/2026', '3.00'],
    ])
  })

  it('keeps apart a proforma and an invoice with the same number on one day', () => {
    const same: Pick<MatchReport, 'by_day' | 'invoices'> = {
      by_day: [
        day('2026-10-30', '8.00', [on('5/2026', '8.00')]),
        day('2026-11-03', '8.00', [on('5/2026', '6.00'), on('5/2026', '2.00', 'proforma')]),
        day('2026-12-01', '8.00', [on('5/2026', '8.00', 'proforma')]),
      ],
      invoices: [
        { number: '5/2026', kind: 'proforma', date: null, status: 'draft', payment_status: 'from_unpaid', hours: '10.00' },
        { number: '5/2026', kind: 'invoice', date: '2026-11-30', status: 'issued', payment_status: 'from_unpaid', hours: '14.00' },
      ],
    }
    expect(periodInvoices(same, '2026-11').map((i) => [invoiceLabel(i), i.hours])).toEqual([
      ['proforma 5/2026', '2.00'],
      ['5/2026', '6.00'],
    ])
  })

  it('keeps the CRM’s own hours for an invoice wholly inside the month, even on a day it shares', () => {
    // 3 November: 3 hours on 13/2026 and 5 on 14/2026, both invoices only in November.
    const shared: Pick<MatchReport, 'by_day' | 'invoices'> = {
      by_day: [
        day('2026-11-02', '8.00', [on('13/2026', '8.00')]),
        day('2026-11-03', '8.00', [on('13/2026', '3.00'), on('14/2026', '5.00')]),
      ],
      invoices: [
        { number: '14/2026', kind: 'invoice', date: null, status: 'draft', payment_status: 'from_unpaid', hours: '5.00' },
        { number: '13/2026', kind: 'invoice', date: '2026-11-30', status: 'issued', payment_status: 'from_unpaid', hours: '11.00' },
      ],
    }
    expect(periodInvoices(shared, '2026-11').map((i) => i.hours)).toEqual(['5.00', '11.00'])
  })
})

describe('the progress line', () => {
  it('reads the total against the letter’s days', () => {
    expect(progressLine(REPORT)).toBe('96 ore, 12 giorni su 40 previsti (30%)')
    expect(
      progressLine({ hours_total: '7.50', equivalent_days: '0.94', expected_days: 1, progress: '93.75' }),
    ).toBe('7,5 ore, 0,94 giorni su 1 previsto (93,75%)')
  })

  it('says the hours and the days alone without an estimate', () => {
    expect(progressLine({ ...REPORT, expected_days: null, progress: null })).toBe(
      '96 ore, 12 giorni',
    )
    expect(progressLine({ hours_total: '8.00', equivalent_days: '1.00', expected_days: null, progress: null })).toBe(
      '8 ore, 1 giorno',
    )
    expect(progressLine({ hours_total: '1.00', equivalent_days: '0.13', expected_days: null, progress: null })).toBe(
      '1 ora, 0,13 giorni',
    )
  })

  it('says nothing is done yet on an empty report', () => {
    expect(
      progressLine({ hours_total: '0.00', equivalent_days: '0.00', expected_days: 40, progress: '0.00' }),
    ).toBe('0 ore, 0 giorni su 40 previsti (0%)')
  })
})
