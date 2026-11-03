import { describe, expect, it } from 'vitest'
import {
  formatAmount,
  expensePolicyBody,
  expenseStanding,
  nextStates,
  readExpensePolicy,
  stateLabel,
} from './labels'
import type { ContractExpense } from './queries'

describe('nextStates', () => {
  it('offers every state but the one the contract is in', () => {
    expect(nextStates('draft')).toEqual(['active', 'terminated', 'expired'])
    expect(nextStates('active')).toEqual(['draft', 'terminated', 'expired'])
  })

  it('shows a state this build does not know as it is stored', () => {
    expect(stateLabel('sospeso')).toBe('sospeso')
  })
})

describe('the expense policy', () => {
  it('round-trips the three tagged shapes', () => {
    for (const policy of [
      { kind: 'not_reimbursable' },
      { kind: 'reimbursable', requires_pre_authorization: true },
      { kind: 'reimbursable_with_cap', amount_cap: '500.00', requires_pre_authorization: false },
    ] as const) {
      const body = expensePolicyBody(policy)
      expect(body).toEqual(policy)
      expect(readExpensePolicy(body)).toMatchObject(policy)
    }
  })

  it('does not write a cap or an authorisation flag on a policy that has none', () => {
    expect(
      expensePolicyBody({ kind: 'not_reimbursable', amount_cap: '9', requires_pre_authorization: true }),
    ).toEqual({ kind: 'not_reimbursable' })
  })

  it('reads a numeric cap as the string a form can show', () => {
    expect(readExpensePolicy({ kind: 'reimbursable_with_cap', amount_cap: 500 })?.amount_cap).toBe('500')
  })

  it('reads a document another writer made as nothing, never as a guess', () => {
    expect(readExpensePolicy({ type: 'not_reimbursable' })).toBeNull()
  })
})

describe('expenseStanding', () => {
  const base = { reimbursable: true, invoice_line_id: null } as ContractExpense

  it('says an unbilled rebillable expense is waiting to be invoiced', () => {
    expect(expenseStanding(base)).toEqual({ tone: 'gold', label: 'Da fatturare' })
  })

  it('says a billed one is on an invoice, whatever the policy says now', () => {
    expect(expenseStanding({ ...base, invoice_line_id: 'l1', reimbursable: false }).label).toBe(
      'In fattura',
    )
  })

  it('says an expense the policy does not rebill is not rebillable', () => {
    expect(expenseStanding({ ...base, reimbursable: false }).label).toBe('Non rimborsabile')
  })
})

describe('formatAmount', () => {
  it('formats euros and shows any other currency as stated, never as euros', () => {
    expect(formatAmount('400.00', 'EUR').replace(/\s/g, ' ')).toBe('400,00 €')
    expect(formatAmount('400.00', 'USD')).toBe('400.00 USD')
  })
})
