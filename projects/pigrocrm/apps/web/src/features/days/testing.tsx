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
    titolo: 'Consulenza architetturale',
    inizio: '2026-01-01',
    fine: null,
    stato: 'attivo',
    requires_prior_approval: false,
    ...overrides,
  }
}

export function rateCard(overrides: Partial<RateCard> = {}): RateCard {
  return {
    id: 'rc1',
    contract_id: CONTRACT_ID,
    valido_da: '2026-01-01',
    valido_a: null,
    tipo: 'giornaliero',
    importo: '450.00',
    unita: 'giorno',
    frazioni_ammesse: ['1.00', '0.50'],
    ore_minime: null,
    periodo_erogazione: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function row(overrides: Partial<WorkUnitRow> = {}): WorkUnitRow {
  return {
    id: DAY_ID,
    contract_id: CONTRACT_ID,
    data: '2026-03-02',
    quantita: '0.50',
    descrizione: 'Revisione architettura servizi',
    stato: 'lavorato',
    approval_id: null,
    invoice_line_id: null,
    note: null,
    created_at: '2026-03-02T09:00:00Z',
    updated_at: '2026-03-02T09:00:00Z',
    contract_titolo: 'Consulenza architetturale',
    customer_id: CUSTOMER_ID,
    customer_nome: 'Acme S.r.l.',
    ...overrides,
  }
}

export function detail(overrides: Partial<WorkUnitDetail> = {}): WorkUnitDetail {
  return {
    ...row(),
    requires_prior_approval: false,
    contratto_inizio: '2026-01-01',
    contratto_fine: null,
    fuori_termine: null,
    approval: null,
    transizioni: [
      {
        id: 't1',
        work_unit_id: DAY_ID,
        stato_precedente: null,
        stato_nuovo: 'approvato',
        attore: { kind: 'human', id: 'u1' },
        motivo: 'creazione',
        created_at: '2026-03-01T09:00:00Z',
        seq: 1,
      },
    ],
    azioni: ['lavorato', 'revocato'],
    stato: 'approvato',
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
