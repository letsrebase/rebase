import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { NotificationsPanel } from './NotificationsPanel'
import { RemindersPanel } from './RemindersPanel'

// The editor itself is `features/reminders/ReminderPreferences.test.tsx`'s; here only which
// layer each settings section hands it is under test.
vi.mock('@/features/reminders/ReminderPreferences', () => ({
  ReminderPreferences: ({ scope }: { scope: string }) => <p>editor dello scope {scope}</p>,
}))
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Cora', email: 'c@pigro.it', role: 'readonly', weekly_digest: true },
  }),
}))

function renderPanel(element: React.ReactElement) {
  render(<QueryClientProvider client={new QueryClient()}>{element}</QueryClientProvider>)
}

describe('the Notifiche section', () => {
  it("holds the weekly digest switch and a person's own reminder settings", () => {
    renderPanel(<NotificationsPanel />)

    expect(screen.getByRole('switch', { name: 'Ricevi il resoconto settimanale' })).toBeChecked()
    expect(screen.getByText('editor dello scope me')).toBeInTheDocument()
    expect(screen.queryByText('editor dello scope space')).not.toBeInTheDocument()
  })
})

describe('the Promemoria section', () => {
  it("holds the space's defaults and nothing personal", () => {
    renderPanel(<RemindersPanel />)

    expect(screen.getByText('editor dello scope space')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('reads who changed the defaults and when, with the reminder named as the section names it', async () => {
    const answers: Record<string, unknown> = {
      '/api/reminders/preferences': {
        types: [{ key: 'invoice_overdue', label: 'Fattura scaduta' }],
      },
      '/api/reminders/defaults/timeline': [
        {
          id: 'e2',
          kind: 'defaults_reset',
          occurred_at: '2026-11-09T10:15:00Z',
          actor_type: 'user',
          actor_name: 'Ivan Rossi',
          payload: { type: 'general' },
        },
        {
          id: 'e1',
          kind: 'defaults_updated',
          occurred_at: '2026-11-09T09:00:00Z',
          actor_type: 'user',
          actor_name: 'Lorenzo Fiore',
          payload: { type: 'invoice_overdue', changed: ['ladder'] },
        },
      ],
    }
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      Promise.resolve({ data: answers[path], response: new Response(null, { status: 200 }) })) as never)
    renderPanel(<RemindersPanel />)

    const history = await screen.findByRole('region', { name: 'Storico delle modifiche' })
    expect(await within(history).findByText('Lorenzo Fiore')).toBeInTheDocument()
    expect(within(history).getByText('Ivan Rossi')).toBeInTheDocument()
    expect(within(history).getByText('Promemoria: valori predefiniti modificati')).toBeInTheDocument()
    expect(within(history).getByText('Promemoria: valori predefiniti ripristinati')).toBeInTheDocument()
    expect(await within(history).findByText(/Fattura scaduta · Campi modificati/)).toBeInTheDocument()
    expect(within(history).getByText('Ore di silenzio e riepilogo')).toBeInTheDocument()
  })
})
