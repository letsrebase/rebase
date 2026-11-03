import { describe, expect, it } from 'vitest'
import { proposalToFormValues, toConvertBody, type ContractProposal } from './convertDeal'

const PROPOSAL = {
  deal_id: 'd1',
  customer_id: 'c1',
  legal_entity_id: 'a1',
  title: 'Piattaforma',
  start: '2026-02-01',
  end: null,
  cards: [],
} as ContractProposal

describe('proposalToFormValues', () => {
  it('takes the title, the start and an empty end from the deal and the rest from the contract form', () => {
    const values = proposalToFormValues(PROPOSAL)
    expect([values.title, values.start, values.end]).toEqual(['Piattaforma', '2026-02-01', ''])
    expect([values.renewal_type, values.termination_notice_days, values.billing_cadence]).toEqual([
      'none',
      '30',
      'monthly',
    ])
  })

  it('carries the end the deal proposes', () => {
    expect(proposalToFormValues({ ...PROPOSAL, end: '2027-01-31' }).end).toBe('2027-01-31')
  })
})

describe('toConvertBody', () => {
  it('sends exactly the eight fields a conversion takes, an empty end as null', () => {
    const body = toConvertBody(proposalToFormValues(PROPOSAL))
    expect(body).toEqual({
      title: 'Piattaforma',
      start: '2026-02-01',
      end: null,
      renewal_type: 'none',
      renewal_notice_days: null,
      termination_notice_days: 30,
      billing_cadence: 'monthly',
      expense_policy: { kind: 'not_reimbursable' },
    })
  })

  it('reads a notice and a capped expense policy as the contract form does', () => {
    const body = toConvertBody({
      ...proposalToFormValues(PROPOSAL),
      renewal_type: 'tacit',
      renewal_notice_days: '60',
      expenses_kind: 'reimbursable_with_cap',
      expenses_cap: '500,00',
      expenses_preauthorization: true,
    })
    expect(body.renewal_notice_days).toBe(60)
    expect(body.expense_policy).toEqual({
      kind: 'reimbursable_with_cap',
      requires_pre_authorization: true,
      amount_cap: '500.00',
    })
  })
})
