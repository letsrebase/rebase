import type { Engagement } from './labels'

/** The fixtures of the list, the table and the form's tests: invented people, invented amounts. */
export function engagement(overrides: Partial<Engagement> = {}): Engagement {
  return {
    id: 'e1',
    hub_match_id: null,
    freelancer_id: 'f1',
    client_organization_id: 'c1',
    deal_id: null,
    contract_id: null,
    redacted: false,
    role: 'Backend developer',
    starts_on: '2026-11-02',
    ends_on: null,
    days_per_week: '4.0',
    fee: '450.00',
    fee_unit: 'day',
    expected_days: null,
    letter_type: 'ordinary',
    payment_rule: 'fixed_term',
    payment_days: 30,
    payment_end_of_month: false,
    collection_days: null,
    state: 'draft',
    letter_number: null,
    missing: [],
    warnings: [],
    waiting: [{ code: 'letter_not_created' }],
    freelancer_name: 'Giulia Martini',
    client_name: 'Alfa Logistica S.r.l.',
    letter_document_id: null,
    created_at: '2026-11-01T09:00:00Z',
    updated_at: '2026-11-01T09:00:00Z',
    ...overrides,
  }
}
