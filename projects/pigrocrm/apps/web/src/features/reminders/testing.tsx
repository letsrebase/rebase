import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import type { Mock } from 'vitest'
import { api } from '@/lib/api'
import type { Reminder } from './fields'

/** The shared fixtures and fake server of this folder's tests. */

export const ACME_ACTIVITY_ID = '3f0c2a10-0000-4000-8000-000000000001'
export const ROSSI_DEAL_ID = '3f0c2a10-0000-4000-8000-000000000002'
export const TIMER_ID = '3f0c2a10-0000-4000-8000-000000000003'
export const PROPOSAL_ID = '3f0c2a10-0000-4000-8000-000000000004'

export function reminder(overrides: Partial<Reminder> = {}): Reminder {
  return {
    key: `attivita_overdue:${ACME_ACTIVITY_ID}`,
    type: 'attivita_overdue',
    label: 'Attività scaduta',
    severity: 'critical',
    measure: 16,
    title: 'Attività scaduta: Mandare il preventivo ad Acme',
    detail: 'Scaduta da 16 giorni (il 10/10/2026).',
    entity: 'attivita',
    entity_id: ACME_ACTIVITY_ID,
    customer_id: null,
    status: 'open',
    snoozed_until: null,
    delivered_at: '2026-10-26T06:00:00Z',
    ...overrides,
  }
}

/** One of each severity, severest first, the order the API sends them in. */
export const CRITICAL = reminder()
export const SERIOUS = reminder({
  key: `deal_past_close:${ROSSI_DEAL_ID}`,
  type: 'deal_past_close',
  label: 'Deal oltre la chiusura prevista',
  severity: 'serious',
  title: 'Deal oltre la chiusura prevista: Rinnovo manutenzione Rossi',
  detail: 'La chiusura prevista era il 05/10/2026.',
  entity: 'deal',
  entity_id: ROSSI_DEAL_ID,
})
export const WARNING = reminder({
  key: `timer_running_long:${TIMER_ID}`,
  type: 'timer_running_long',
  label: 'Timer acceso da molto',
  severity: 'warning',
  title: 'Timer acceso da 11 ore',
  detail: 'Su Rinnovo manutenzione Rossi.',
  entity: 'timer',
  entity_id: TIMER_ID,
})

export function renderWithQuery(element: ReactElement): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)
  return client
}

/** What `openapi-fetch` resolves with for a 200. */
export function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) }
}

/**
 * A server that remembers: `GET /api/reminders` answers the current list and the two
 * writes change it the way the real service does, so a test follows a click to what the
 * next read says, which is the thing the page and the badge both depend on.
 * `snoozedUntil` is what the API answers for any option.
 */
export function fakeReminders(initial: Reminder[], snoozedUntil = '2026-10-27') {
  let list = initial
  const GET = api.GET as unknown as Mock
  const POST = api.POST as unknown as Mock
  GET.mockImplementation((path: string) =>
    Promise.resolve(path === '/api/reminders' ? ok(list) : ok(undefined)),
  )
  POST.mockImplementation(
    (path: string, init: { body: { key: string; severity: string; until?: string } }) => {
      const { key, severity } = init.body
      if (path === '/api/reminders/snooze') {
        list = list.map((row) =>
          row.key === key ? { ...row, status: 'snoozed', snoozed_until: snoozedUntil } : row,
        )
        return Promise.resolve(
          ok({ key, acknowledged_severity: null, snoozed_severity: severity, snoozed_until: snoozedUntil }),
        )
      }
      list = list.map((row) => (row.key === key ? { ...row, status: 'acknowledged' } : row))
      return Promise.resolve(
        ok({ key, acknowledged_severity: severity, snoozed_severity: null, snoozed_until: null }),
      )
    },
  )
}
