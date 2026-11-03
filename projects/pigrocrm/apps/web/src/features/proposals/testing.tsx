import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import type { Proposal } from './fields'

/** The shared fixtures and render helper of this folder's component tests. */

export const CONTRACT_ID = '5b2c70c2-0000-4000-8000-000000000001'
export const CUSTOMER_ID = '5b0d7190-0000-4000-8000-000000000002'

export function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 'p1',
    document_id: 'd1',
    contract_id: CONTRACT_ID,
    target_type: 'work_unit',
    proposed_fields: {
      contract_id: CONTRACT_ID,
      date: '2026-11-05',
      quantity: 1,
      description: 'Sopralluogo al magazzino di Verona',
      approval: {
        channel: 'email',
        sender: 'giulia.bianchi@nordwind.example',
        received_at: '2026-10-21T09:12:00Z',
      },
    },
    excerpt: 'Confermo la giornata di giovedì 5 novembre per il sopralluogo.',
    excerpt_type: 'quoted',
    confidence: '0.93',
    confidence_reason: 'Data e attività esplicite.',
    status: 'pending',
    accepted_fields: null,
    result_id: null,
    decided_by: null,
    decided_at: null,
    created_at: '2026-10-26T12:00:00Z',
    updated_at: '2026-10-26T12:00:00Z',
    ...overrides,
  }
}

export const CONTRACT_PROPOSAL = proposal({
  id: 'p2',
  contract_id: null,
  target_type: 'contract',
  proposed_fields: {
    contract: {
      customer_id: CUSTOMER_ID,
      title: 'Consulenza fiscale 2027',
      start: '2027-01-01',
      end: null,
      renewal_type: 'none',
      termination_notice_days: 30,
      billing_cadence: 'monthly',
      currency: 'EUR',
    },
    rate_card: {
      type: 'fixed_recurring',
      unit: 'month',
      amount: '1800.00',
      valid_from: '2027-01-01',
    },
  },
  excerpt: 'Il presente contratto ha durata annuale.',
  confidence: '0.78',
  confidence_reason: null,
})

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
