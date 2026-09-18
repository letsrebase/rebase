import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

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
  it('shows the current user as plain text', () => {
    renderPanel()

    expect(screen.getByText('Cora Collaboratrice')).toBeInTheDocument()
    expect(screen.getByText('collab@pigro.it')).toBeInTheDocument()
    expect(screen.getByText('collaboratore')).toBeInTheDocument()
  })

  /**
   * The panel this task exists for: reachable by any role (`ProfilePanel` calls no
   * `/api/users` endpoint, only `PATCH /api/auth/me`), and the switch reads the
   * current user's own `digest_settimanale` -- no admin-only list involved.
   */
  it('reads digest_settimanale from the current user, and toggling it sends the PATCH', async () => {
    vi.mocked(api.PATCH).mockReturnValueOnce(
      Promise.resolve(ok({ ...USER, digest_settimanale: false })),
    )
    renderPanel()

    const toggle = screen.getByRole('switch', { name: 'Ricevi il resoconto settimanale' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')

    await userEvent.click(toggle)

    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith(
        '/api/auth/me',
        expect.objectContaining({ body: { digest_settimanale: false } }),
      ),
    )
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('Resoconto settimanale aggiornato'),
    )
  })

  it('shows the usual error toast when the PATCH fails', async () => {
    vi.mocked(api.PATCH).mockReturnValueOnce(
      Promise.resolve(failed({ code: 'http_error', detail: 'Il server non risponde.' }, 503)),
    )
    renderPanel()

    await userEvent.click(screen.getByRole('switch', { name: 'Ricevi il resoconto settimanale' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Il server non risponde.'))
  })
})
