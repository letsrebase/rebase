import { describe, expect, it } from 'vitest'
import {
  contractToFormValues,
  defaultContractFormValues,
  toContractBody,
} from './contractFormValues'
import type { Contract } from './queries'

const CONTRACT: Contract = {
  id: 'c1',
  customer_id: 'cust-1',
  legal_entity_id: 'a1',
  title: 'Sviluppo piattaforma',
  start: '2026-01-01',
  end: '2026-12-31',
  renewal_type: 'tacit',
  renewal_notice_days: 60,
  termination_notice_days: 30,
  payment_days: 30,
  payment_end_of_month: true,
  billing_cadence: 'monthly',
  currency: 'EUR',
  requires_prior_approval: true,
  applies_social_charge: false,
  customer_share_cap: null,
  expense_policy: { kind: 'reimbursable_with_cap', amount_cap: '500.00' },
  status: 'active',
  previous_contract_id: null,
  note: null,
  custom_fields: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const nothingRendered = () => false

describe('toContractBody, the cap on the customer’s share', () => {
  it('sends null on create when the box is blank, and the typed percentage otherwise', () => {
    const base = { ...defaultContractFormValues(), title: 'Manutenzione', start: '2026-11-01' }
    expect(toContractBody(base, undefined, nothingRendered)).toMatchObject({
      customer_share_cap: null,
    })
    expect(
      toContractBody({ ...base, customer_share_cap: ' 50,5 ' }, undefined, nothingRendered),
    ).toMatchObject({ customer_share_cap: '50.5' })
  })

  it('reads the stored percentage into the box, and a missing one as blank', () => {
    expect(contractToFormValues({ ...CONTRACT, customer_share_cap: '50.00' }).customer_share_cap).toBe(
      '50.00',
    )
    expect(contractToFormValues(CONTRACT).customer_share_cap).toBe('')
  })

  it('sends only the cap when only the cap changed', () => {
    const initial = contractToFormValues({ ...CONTRACT, customer_share_cap: '50.00' })
    expect(toContractBody({ ...initial, customer_share_cap: '40' }, initial, nothingRendered)).toEqual({
      customer_share_cap: '40',
    })
  })

  it('clears the cap with null when the box is emptied on edit', () => {
    const initial = contractToFormValues({ ...CONTRACT, customer_share_cap: '50.00' })
    expect(toContractBody({ ...initial, customer_share_cap: '' }, initial, nothingRendered)).toEqual({
      customer_share_cap: null,
    })
  })

  it('does not resend a cap nobody touched', () => {
    const initial = contractToFormValues({ ...CONTRACT, customer_share_cap: '50.00' })
    expect(toContractBody(initial, initial, nothingRendered)).toEqual({})
  })
})

describe('toContractBody', () => {
  it('sends every field on create, with the payment terms null when they follow the customer', () => {
    const values = {
      ...defaultContractFormValues(),
      title: '  Manutenzione  ',
      start: '2026-11-01',
    }
    expect(toContractBody(values, undefined, nothingRendered)).toMatchObject({
      title: 'Manutenzione',
      start: '2026-11-01',
      end: null,
      renewal_type: 'none',
      renewal_notice_days: null,
      termination_notice_days: 30,
      payment_days: null,
      payment_end_of_month: null,
      expense_policy: { kind: 'not_reimbursable' },
    })
  })

  it('sends only the notice period when only the notice period changed', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, termination_notice_days: '90' }, initial, nothingRendered)
    expect(body).toEqual({ termination_notice_days: 90 })
  })

  it('sends both halves of the payment terms when one moved', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, payment_days: '60' }, initial, nothingRendered)
    expect(body).toEqual({ payment_days: 60, payment_end_of_month: true })
  })

  it('clears both payment terms when the contract goes back to the customer’s', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, payment_custom: false }, initial, nothingRendered)
    expect(body).toEqual({ payment_days: null, payment_end_of_month: null })
  })

  it('sends the renewal type with its notice, and drops the notice for «nessuno»', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, renewal_type: 'none' }, initial, nothingRendered)
    expect(body).toEqual({ renewal_type: 'none', renewal_notice_days: null })
  })

  it('writes the expense policy as the tagged shape the trigger reads', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody(
      { ...initial, expenses_kind: 'reimbursable', expenses_preauthorization: true },
      initial,
      nothingRendered,
    )
    expect(body).toEqual({
      expense_policy: { kind: 'reimbursable', requires_pre_authorization: true },
    })
  })

  it('clears an emptied end date with null', () => {
    const initial = contractToFormValues(CONTRACT)
    expect(toContractBody({ ...initial, end: '' }, initial, nothingRendered)).toEqual({ end: null })
  })

  it('leaves a stored custom value whose definition is archived out of the body', () => {
    const initial = contractToFormValues({ ...CONTRACT, custom_fields: { 'vecchio': 'x' } })
    expect(toContractBody(initial, initial, nothingRendered)).toEqual({})
  })

  it('sends a cleared rendered custom field as null so the service removes it', () => {
    const initial = contractToFormValues({ ...CONTRACT, custom_fields: { 'referente': 'Rossi' } })
    const body = toContractBody(
      { ...initial, custom: { 'referente': '' } },
      initial,
      (key) => key === 'referente',
    )
    expect(body).toEqual({ custom_fields: { 'referente': null } })
  })
})

describe('toContractBody review fixes', () => {
  it('normalises a decimal comma in the cap', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, expenses_cap: '600,50' }, initial, () => false)
    expect(body).toEqual({
      expense_policy: { kind: 'reimbursable_with_cap', requires_pre_authorization: false, amount_cap: '600.50' },
    })
  })

  it('sends an emptied disdetta notice as null so the API answers', () => {
    const initial = contractToFormValues(CONTRACT)
    expect(
      toContractBody({ ...initial, termination_notice_days: '' }, initial, () => false),
    ).toEqual({ termination_notice_days: null })
  })

  it('does not resend a custom value nobody touched', () => {
    const initial = contractToFormValues({ ...CONTRACT, custom_fields: { 'referente': 'Rossi' } })
    expect(toContractBody(initial, initial, (key) => key === 'referente')).toEqual({})
  })
})
