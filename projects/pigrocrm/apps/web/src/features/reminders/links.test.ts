import { describe, expect, it } from 'vitest'
import { defaultDashboardSearch } from '@/features/dashboard/search'
import { handledLine, subjectOf } from './fields'
import { recordLink } from './links'
import { CRITICAL, reminder } from './testing'

const ID = '3f0c2a10-0000-4000-8000-0000000000aa'

describe('recordLink', () => {
  it.each([
    ['attivita', { to: '/app/activities', search: { apri: ID } }],
    ['deal', { to: '/app/deal/$dealId', params: { dealId: ID } }],
    ['customer', { to: '/app/customers/$customerId', params: { customerId: ID } }],
    ['invoice', { to: '/app/invoices/$invoiceId', params: { invoiceId: ID } }],
    ['proforma', { to: '/app/invoices/$invoiceId', params: { invoiceId: ID } }],
    ['contract', { to: '/app/contracts/$contractId', params: { contractId: ID } }],
    ['fiscal', { to: '/app', search: { ...defaultDashboardSearch(), tab: 'anno' } }],
    ['proposal', { to: '/app/proposals/$proposalId', params: { proposalId: ID } }],
    ['timer', { to: '/app/hours' }],
    // A late job opens Esecuzioni: there is no record, and the section lists them all.
    ['job', { to: '/app/settings/jobs' }],
  ])('opens a %s where the app has its page', (entity, expected) => {
    expect(recordLink({ entity, entity_id: ID })).toEqual(expected)
  })

  it.each(['work_unit', 'google_connection', 'period', 'something_new', '', 'constructor', 'toString'])(
    'gives no link for the kind «%s», which has no page to open',
    (entity) => {
      expect(recordLink({ entity, entity_id: ID })).toBeNull()
    },
  )
})

describe('handledLine', () => {
  it('says what was done, and a snooze with its day, read as written and not as UTC', () => {
    expect(handledLine({ ...CRITICAL, status: 'acknowledged' })).toBe('Visto')
    expect(handledLine(reminder({ status: 'snoozed', snoozed_until: '2026-11-01' }))).toBe(
      'Rimandato fino al 01/11/2026',
    )
  })
})

describe('subjectOf', () => {
  it('drops the kind the API puts in front of every title, because the row shows it above', () => {
    expect(
      subjectOf({ label: 'Attività scaduta', title: 'Attività scaduta: Mandare il preventivo ad Acme' }),
    ).toBe('Mandare il preventivo ad Acme')
  })

  it('shows a title that does not start that way whole, and never an empty one', () => {
    expect(subjectOf({ label: 'Timer', title: 'Timer acceso da 11 ore' })).toBe('Timer acceso da 11 ore')
    expect(subjectOf({ label: 'Deal fermo', title: 'Deal fermo: ' })).toBe('Deal fermo: ')
  })
})
