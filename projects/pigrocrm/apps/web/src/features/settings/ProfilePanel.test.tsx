import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { toast } from '@rebase/ui/sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ProfilePanel } from './ProfilePanel'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/lib/auth', () => ({ useAuth: vi.fn() }))

const USER = {
  id: 'u1',
  email: 'collab@pigro.it',
  nome: 'Cora Collaboratrice',
  ruolo: 'collaboratore' as const,
  attivo: true,
  digest_settimanale: true,
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ProfilePanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(toast.error).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(useAuth).mockReturnValue({
    user: USER,
    isLoading: false,
    login: vi.fn(),
    enterWithLink: vi.fn(),
    logout: vi.fn(),
  } as never)
})

describe('ProfilePanel', () => {
  it('shows the current user as plain text, and no digest switch: that is Notifiche\'s', () => {
    renderPanel()

    expect(screen.getByText('Cora Collaboratrice')).toBeInTheDocument()
    expect(screen.getByText('collab@pigro.it')).toBeInTheDocument()
    expect(screen.getByText('collaboratore')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
})
