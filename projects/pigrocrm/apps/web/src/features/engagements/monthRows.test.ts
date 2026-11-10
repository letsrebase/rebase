import { describe, expect, it } from 'vitest'
import type { DaysReport, EngagementPayment, ReportMonth } from './labels'
import { lateWords, paymentView, planMonths } from './monthRows'

function payment(overrides: Partial<EngagementPayment>): EngagementPayment {
  return {
    due_item_id: 'd1',
    month: null,
    state: 'forecast',
    amount: '1800.00',
    settled: null,
    remaining: null,
    currency: 'EUR',
    due_date: null,
    due_date_estimated: false,
    waiting_for_invoice: null,
    description: null,
    ...overrides,
  }
}

function reportOf(months: string[]): DaysReport {
  return {
    engagement_id: 'e1',
    unit: 'day',
    fee_unit: 'day',
    date_from: '2026-09-01',
    date_to: '2026-11-10',
    available: true,
    note: null,
    days: [],
    months: months.map(
      (month): ReportMonth => ({
        month,
        label: month.slice(0, 7),
        monthly_report: null,
        quantity: '0',
        held_quantity: '0',
        payable_quantity: '0',
        payable_amount: null,
      }),
    ),
    total_quantity: '0',
    payable_quantity: '0',
    held_quantity: '0',
    payable_amount: null,
  }
}

describe('planMonths', () => {
  it('joins the payments to the report months by their month, oldest month first', () => {
    const plan = planMonths(
      reportOf(['2026-10-01', '2026-09-01']),
      [payment({ due_item_id: 'a', month: '2026-10-01' })],
      '2026-11-10',
    )
    expect(plan.rows.map((row) => row.key)).toEqual(['2026-09', '2026-10'])
    expect(plan.rows[1]?.payments.map((item) => item.due_item_id)).toEqual(['a'])
    expect(plan.rows.every((row) => row.ended)).toBe(true)
  })

  it('counts the forecasts after the last report month in one summary and keeps the others apart', () => {
    const plan = planMonths(
      reportOf(['2026-10-01']),
      [
        payment({ due_item_id: 'a', month: '2026-12-01' }),
        payment({ due_item_id: 'b', month: '2027-01-01' }),
        payment({ due_item_id: 'c', month: '2027-02-01' }),
        payment({ due_item_id: 'd', month: null, state: 'expected' }),
      ],
      '2026-11-10',
    )
    expect(plan.future).toEqual({ months: 3, firstMonth: '2026-12-01', lastMonth: '2027-02-01' })
    expect(plan.others.map((item) => item.due_item_id)).toEqual(['d'])
    expect(plan.rows).toHaveLength(1)
  })

  it('gives a month only a payment names a row of its own when the report is not read', () => {
    const plan = planMonths(undefined, [payment({ month: '2026-12-01' })], '2026-11-10')
    expect(plan.rows.map((row) => row.key)).toEqual(['2026-12'])
    expect(plan.rows[0]?.report).toBeNull()
    expect(plan.rows[0]?.state).toBe('not_yet')
    expect(plan.future).toBeNull()
  })

  it('reads the current month as «Non ancora» and a past one as «Non ricevuto»', () => {
    const plan = planMonths(reportOf(['2026-10-01', '2026-11-01']), [], '2026-11-10')
    expect(plan.rows.map((row) => row.state)).toEqual(['not_received', 'not_yet'])
  })
})

describe('payments', () => {
  it('reads an expected payment past its date as late, with the days behind', () => {
    const late = payment({ state: 'expected', due_date: '2026-11-05' })
    expect(paymentView(late, '2026-11-10')).toBe('late')
    expect(lateWords(late, '2026-11-10')).toBe('Scaduto da 5 giorni')
    expect(lateWords(payment({ state: 'expected', due_date: '2026-11-09' }), '2026-11-10')).toBe('Scaduto da 1 giorno')
  })

  it('keeps a forecast a forecast whatever its date and a settled one paid', () => {
    expect(paymentView(payment({ due_date: '2020-01-01' }), '2026-11-10')).toBe('forecast')
    expect(paymentView(payment({ state: 'settled', due_date: '2020-01-01' }), '2026-11-10')).toBe('paid')
    expect(paymentView(payment({ state: 'expected', due_date: '2026-11-10' }), '2026-11-10')).toBe('to_pay')
  })
})
