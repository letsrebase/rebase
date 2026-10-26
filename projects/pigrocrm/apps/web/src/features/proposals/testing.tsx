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
    target_type: 'giornata',
    campi_proposti: {
      contract_id: CONTRACT_ID,
      data: '2026-11-05',
      quantita: 1,
      descrizione: 'Sopralluogo al magazzino di Verona',
      approvazione: {
        canale: 'email',
        mittente: 'giulia.bianchi@nordwind.example',
        ricevuto_il: '2026-10-21T09:12:00Z',
      },
    },
    estratto: 'Confermo la giornata di giovedì 5 novembre per il sopralluogo.',
    tipo_estratto: 'citato',
    confidenza: '0.93',
    motivo_confidenza: 'Data e attività esplicite.',
    stato: 'in_attesa',
    campi_accettati: null,
    id_risultato: null,
    deciso_da: null,
    deciso_il: null,
    created_at: '2026-10-26T12:00:00Z',
    updated_at: '2026-10-26T12:00:00Z',
    ...overrides,
  }
}

export const CONTRACT_PROPOSAL = proposal({
  id: 'p2',
  contract_id: null,
  target_type: 'contratto',
  campi_proposti: {
    contract: {
      customer_id: CUSTOMER_ID,
      titolo: 'Consulenza fiscale 2027',
      inizio: '2027-01-01',
      fine: null,
      tipo_rinnovo: 'nessuno',
      preavviso_disdetta_giorni: 30,
      cadenza_fatturazione: 'mensile',
      divisa: 'EUR',
    },
    rate_card: {
      tipo: 'ricorrente_fisso',
      unita: 'mese',
      importo: '1800.00',
      valido_da: '2027-01-01',
    },
  },
  estratto: 'Il presente contratto ha durata annuale.',
  confidenza: '0.78',
  motivo_confidenza: null,
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
