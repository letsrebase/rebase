/**
 * The tab «Scadenziario» is the cash schedule under the Home's header (REB-1025): its states are
 * tested with the schedule (`features/cash/schedule/CashSchedule.test.tsx`). Here only what the
 * tab itself promises: the same body «Cassa» mounts, reading the schedule and the owed sections,
 * with the sidebar's azienda as the one parameter.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { ReceivablesTab } from './ReceivablesTab'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u-1', role: 'admin' } }),
  useCan: () => true,
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

const SCHEDULE = {
  calculated_at: '2026-11-10T11:42:00Z',
  today: '2026-11-10',
  legal_entity_id: 'a-2',
  horizon_weeks: 13,
  horizon_options: [4, 13, 26],
  profile: 'company',
  cash_enabled: false,
  currency: 'EUR',
  show_outgoing: true,
  show_balance: false,
  accounts: null,
  total_balance: null,
  late_receivable: { count: 0, total: '0.00', oldest_days: null },
  late_payable: { count: 0, total: '0.00', oldest_days: null },
  weeks: [],
  taxes: null,
  alerts: [],
  has_open_items: false,
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockResolvedValue(ok(SCHEDULE))
})

describe('ReceivablesTab', () => {
  it('is the cash schedule, with the sidebar’s azienda as its one parameter', async () => {
    const value: LegalEntityValue = {
      legal_entities: [],
      selected: 'a-2',
      select: vi.fn(),
      several: true,
      scoped: false,
      pinned: false,
      loaded: true,
      byId: () => undefined,
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={value}>
          <ReceivablesTab />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    expect(await screen.findByText('Niente da incassare né da pagare nelle prossime 13 settimane.')).toBeInTheDocument()
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/cash/schedule', { params: { query: { legal_entity_id: 'a-2' } } }),
    )
    // The old tab's own pictures are gone: no month bars, no exposure by customer.
    expect(screen.queryByText('Incassi attesi per mese di scadenza')).not.toBeInTheDocument()
    expect(screen.queryByText('Esposizione per cliente')).not.toBeInTheDocument()
  })
})
