import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import type { RateCard, WorkUnitDetail, WorkUnitRow } from './labels'

/** The fixtures and render helper this folder's component tests share. */

export const CONTRACT_ID = '5b2c70c2-0000-4000-8000-000000000001'
export const CUSTOMER_ID = '5b0d7190-0000-4000-8000-000000000002'
export const DAY_ID = '7c1d0000-0000-4000-8000-000000000003'

export function contract(overrides: Record<string, unknown> = {}) {
  return {
    id: CONTRACT_ID,
    customer_id: CUSTOMER_ID,
    title: 'Consulenza architetturale',
    start: '2026-01-01',
    end: null,
    status: 'active',
    requires_prior_approval: false,
    ...overrides,
  }
}

export function rateCard(overrides: Partial<RateCard> = {}): RateCard {
  return {
    id: 'rc1',
    contract_id: CONTRACT_ID,
    valid_from: '2026-01-01',
    valid_to: null,
    type: 'daily',
    amount: '450.00',
    unit: 'day',
    allowed_fractions: ['1.00', '0.50'],
    minimum_hours: null,
    delivery_period: null,
    expected_quantity: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function row(overrides: Partial<WorkUnitRow> = {}): WorkUnitRow {
  return {
    id: DAY_ID,
    contract_id: CONTRACT_ID,
    date: '2026-03-02',
    quantity: '0.50',
    description: 'Revisione architettura servizi',
    status: 'worked',
    approval_id: null,
    invoice_line_id: null,
    note: null,
    created_at: '2026-03-02T09:00:00Z',
    updated_at: '2026-03-02T09:00:00Z',
    contract_title: 'Consulenza architetturale',
    customer_id: CUSTOMER_ID,
    customer_name: 'Acme S.r.l.',
    ...overrides,
  }
}

export function detail(overrides: Partial<WorkUnitDetail> = {}): WorkUnitDetail {
  return {
    ...row(),
    requires_prior_approval: false,
    start_contract: '2026-01-01',
    contract_end: null,
    out_of_term: null,
    approval: null,
    transitions: [
      {
        id: 't1',
        work_unit_id: DAY_ID,
        previous_status: null,
        new_status: 'approved',
        actor: { kind: 'human', id: 'u1' },
        reason: 'creazione',
        created_at: '2026-03-01T09:00:00Z',
        seq: 1,
      },
    ],
    actions: ['worked', 'revoked'],
    status: 'approved',
    ...overrides,
  }
}

export function renderWithQuery(element: ReactElement): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)
  return client
}

/** What `openapi-fetch` resolves with: a body, or a problem document and its status. */
export function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) }
}

export function refused(status: number, problem: Record<string, unknown>) {
  return { error: problem, response: new Response(null, { status }) }
}
