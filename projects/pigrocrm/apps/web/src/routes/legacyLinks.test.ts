/**
 * Links already sent (mails, the Monday digest, bookmarks, push payloads) carry the Italian
 * query words of before the English rename of 2026-11-02. Each is read as the word it
 * became, and opens the screen it always did.
 */
import { describe, expect, it } from 'vitest'
import { validateDaysSearch, validateNewDaySearch } from '@/features/days/search'
import { validateProposalsSearch } from '@/features/proposals/search'
import { Route as CalendarRoute } from './app/calendar'
import { Route as DealListRoute } from './app/deal/list'
import { Route as InvoicesRoute } from './app/invoices/index'

type Validate = (search: Record<string, unknown>) => Record<string, unknown>

describe('the Italian query words of links sent before the rename', () => {
  it('?scadute= is ?overdue= on the invoices list', () => {
    const validate = InvoicesRoute.options.validateSearch as Validate
    expect(validate({ 'scadute': 'true' })).toEqual({ overdue: true })
    expect(validate({ overdue: 'true' })).toEqual({ overdue: true })
  })

  it('?da_fatturare= and ?fatturato_non_vinto= are ?to_invoice= and ?invoiced_unwon= on the deals list', () => {
    const validate = DealListRoute.options.validateSearch as Validate
    expect(validate({ 'da_fatturare': 'true', 'fatturato_non_vinto': 'true' })).toMatchObject({
      to_invoice: true,
      invoiced_unwon: true,
    })
  })

  it('?mese= is ?month= on the calendar and the days register', () => {
    const validate = CalendarRoute.options.validateSearch as Validate
    expect(validate({ 'mese': '2026-03' })).toEqual({ month: '2026-03' })
    expect(validateDaysSearch({ 'mese': '2026-03' })).toEqual({ contract: undefined, month: '2026-03' })
  })

  it('?data= is ?date= on the new day form', () => {
    expect(validateNewDaySearch({ 'data': '2026-03-02' })).toEqual({ contract: undefined, date: '2026-03-02' })
  })

  it('?stato=accettata|rifiutata|in_attesa are the proposal tabs', () => {
    expect(validateProposalsSearch({ 'stato': 'accettata' })).toEqual({ status: 'accepted' })
    expect(validateProposalsSearch({ 'stato': 'rifiutata' })).toEqual({ status: 'rejected' })
    expect(validateProposalsSearch({ 'stato': 'in_attesa' })).toEqual({ status: 'pending' })
  })
})
