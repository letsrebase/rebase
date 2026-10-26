import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
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
    user: { id: 'u1', nome: 'Cora', email: 'c@pigro.it', ruolo: 'readonly', digest_settimanale: true },
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
})
