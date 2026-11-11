import { describe, expect, it } from 'vitest'
import {
  STATUS_LABELS,
  STATUS_TONES,
  amountInput,
  kilometresInput,
  mileageLine,
  notes,
  rateInput,
  routeLine,
  stateLine,
  wayLine,
} from './labels'
import { compactSearch, validateExpenseNotesSearch } from './search'
import type { ExpenseNote } from './queries'

const base = {
  status: 'submitted',
  reimbursement_method: null,
  reimbursement_date: null,
  payroll_month: null,
  reimbursed_on: null,
  rejection_reason: null,
  kilometres: null,
  rate_per_km: null,
  route_from: null,
  route_to: null,
} as unknown as ExpenseNote

const note = (overrides: Partial<ExpenseNote>): ExpenseNote => ({ ...base, ...overrides })

describe('the states of a note', () => {
  it('has one tone per state: gold waits, danger is refused, ink is settled', () => {
    expect(STATUS_TONES).toEqual({ submitted: 'gold', approved: 'gold', rejected: 'danger', reimbursed: 'ink' })
    expect(STATUS_LABELS).toEqual({
      submitted: 'Inviata',
      approved: 'Approvata',
      rejected: 'Rifiutata',
      reimbursed: 'Rimborsata',
    })
  })

  it('tells the staff member to wait for an administrator, and an administrator nothing', () => {
    expect(stateLine(note({}), true)).toBe('Aspetta un amministratore')
    expect(stateLine(note({}), false)).toBeNull()
  })

  it('says «Da rimborsare» until the way is chosen, then the way', () => {
    const approved = note({ status: 'approved' })
    expect(stateLine(approved, false)).toBe('Da rimborsare')
    expect(wayLine(approved)).toBeNull()
    expect(
      stateLine(note({ status: 'approved', reimbursement_method: 'transfer', reimbursement_date: '2026-11-10' }), false),
    ).toBe('Bonifico del 10/11/2026')
    expect(
      stateLine(note({ status: 'approved', reimbursement_method: 'payslip', payroll_month: '2026-11-01' }), true),
    ).toBe('In busta paga di novembre')
  })

  it('gives a refusal its reason, and a settled note its date and way', () => {
    expect(stateLine(note({ status: 'rejected', rejection_reason: 'Manca lo scontrino' }), true)).toBe('Manca lo scontrino')
    expect(
      stateLine(
        note({ status: 'reimbursed', reimbursement_method: 'payslip', payroll_month: '2026-11-01', reimbursed_on: '2026-11-12' }),
        false,
      ),
    ).toBe('In busta paga di novembre, il 12/11/2026')
    expect(stateLine(note({ status: 'reimbursed', reimbursement_method: 'transfer', reimbursement_date: '2026-11-10' }), false)).toBe(
      'Bonifico del 10/11/2026',
    )
  })
})

describe('mileage', () => {
  it('writes the route and the kilometres at the typed rate as the server holds them', () => {
    const trip = note({ route_from: 'Milano', route_to: 'Torino', kilometres: '142.00', rate_per_km: '0.4500' })
    expect(routeLine(trip)).toBe('Da Milano a Torino')
    expect(mileageLine(trip)).toBe('142 km a 0,45 €/km')
    expect(mileageLine(note({}))).toBeNull()
  })
})

describe('what a person types', () => {
  it('takes the comma and the dot, and nothing that is not a decimal', () => {
    expect(amountInput('12,50')).toBe('12.50')
    expect(amountInput(' 7 ')).toBe('7')
    expect(amountInput('12,505')).toBeNull()
    expect(amountInput('-3')).toBeNull()
    expect(amountInput('dodici')).toBeNull()
    expect(kilometresInput('142,5')).toBe('142.5')
    expect(rateInput('0,4412')).toBe('0.4412')
    expect(rateInput('0,44123')).toBeNull()
  })

  it('counts notes', () => {
    expect(notes(1)).toBe('1 nota')
    expect(notes(3)).toBe('3 note')
  })
})

describe('the URL', () => {
  it('reads only the tabs, states and ids it knows', () => {
    expect(validateExpenseNotesSearch({ tab: 'closed', status: 'approved', person: 'p', note: 'n' })).toEqual({
      tab: 'closed',
      status: 'approved',
      person: 'p',
      category: undefined,
      legal_entity: undefined,
      note: 'n',
    })
    expect(validateExpenseNotesSearch({ tab: 'all', status: 'x', person: '', note: 3 })).toEqual({
      tab: undefined,
      status: undefined,
      person: undefined,
      category: undefined,
      legal_entity: undefined,
      note: undefined,
    })
  })

  it('carries only what applies', () => {
    expect(compactSearch({ tab: 'closed', note: undefined, person: '' })).toEqual({ tab: 'closed' })
  })
})
