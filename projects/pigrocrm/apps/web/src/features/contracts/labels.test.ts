import { describe, expect, it } from 'vitest'
import {
  expensePolicyBody,
  expenseStanding,
  nextStates,
  readExpensePolicy,
  stateLabel,
} from './labels'
import type { ContractExpense } from './queries'

describe('nextStates', () => {
  it('offers every state but the one the contract is in', () => {
    expect(nextStates('bozza')).toEqual(['attivo', 'terminato', 'scaduto'])
    expect(nextStates('attivo')).toEqual(['bozza', 'terminato', 'scaduto'])
  })

  it('shows a state this build does not know as it is stored', () => {
    expect(stateLabel('sospeso')).toBe('sospeso')
  })
})

describe('the expense policy', () => {
  it('round-trips the three tagged shapes', () => {
    for (const policy of [
      { kind: 'non_rimborsabile' },
      { kind: 'rimborsabile', richiede_preautorizzazione: true },
      { kind: 'rimborsabile_con_tetto', importo_tetto: '500.00', richiede_preautorizzazione: false },
    ] as const) {
      const body = expensePolicyBody(policy)
      expect(body).toEqual(policy)
      expect(readExpensePolicy(body)).toMatchObject(policy)
    }
  })

  it('does not write a cap or an authorisation flag on a policy that has none', () => {
    expect(
      expensePolicyBody({ kind: 'non_rimborsabile', importo_tetto: '9', richiede_preautorizzazione: true }),
    ).toEqual({ kind: 'non_rimborsabile' })
  })

  it('reads a document another writer made as nothing, never as a guess', () => {
    expect(readExpensePolicy({ tipo: 'non_rimborsabile' })).toBeNull()
  })
})

describe('expenseStanding', () => {
  const base = { rimborsabile: true, invoice_line_id: null } as ContractExpense

  it('says an unbilled rebillable expense is waiting to be invoiced', () => {
    expect(expenseStanding(base)).toEqual({ tone: 'gold', label: 'Da fatturare' })
  })

  it('says a billed one is on an invoice, whatever the policy says now', () => {
    expect(expenseStanding({ ...base, invoice_line_id: 'l1', rimborsabile: false }).label).toBe(
      'In fattura',
    )
  })

  it('says an expense the policy does not rebill is not rebillable', () => {
    expect(expenseStanding({ ...base, rimborsabile: false }).label).toBe('Non rimborsabile')
  })
})
