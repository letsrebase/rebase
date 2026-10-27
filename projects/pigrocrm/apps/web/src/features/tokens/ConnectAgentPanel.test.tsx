import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConnectAgentPanel } from './ConnectAgentPanel'
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

function renderPanel(props: { fullAccessByDefault?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ConnectAgentPanel issued={null} onIssued={vi.fn()} {...props} />
    </QueryClientProvider>,
  )
}

function spaceOffersFullAccess(offered: boolean) {
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    Promise.resolve(
      path === '/api/tokens/options' ? ok({ full_access_available: offered }) : ok([]),
    )) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(useAuth).mockReturnValue({ user: { id: 'u1' } } as never)
})

/**
 * REB-810: the invoice, rate and period-lock tools are chosen per token, here as on the
 * tokens page, and closed unless somebody asks for them.
 */
describe('ConnectAgentPanel, full access', () => {
  it('leaves the box unticked and sends the token closed', async () => {
    spaceOffersFullAccess(true)
    vi.mocked(api.POST).mockReturnValueOnce(Promise.resolve(ok({ id: 't', token: 'pgc_x' })))
    renderPanel()

    expect(await screen.findByRole('checkbox', { name: 'Accesso completo' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Crea il token' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/tokens', {
        body: { nome: 'Claude Code', full_access: false },
      }),
    )
  })

  it('sends the token open when the box is ticked', async () => {
    spaceOffersFullAccess(true)
    vi.mocked(api.POST).mockReturnValueOnce(Promise.resolve(ok({ id: 't', token: 'pgc_x' })))
    renderPanel()

    await userEvent.click(await screen.findByRole('checkbox', { name: 'Accesso completo' }))
    await userEvent.click(screen.getByRole('button', { name: 'Crea il token' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/tokens', {
        body: { nome: 'Claude Code', full_access: true },
      }),
    )
  })

  it('starts ticked for a door that cannot work without it, and the person may untick it', async () => {
    spaceOffersFullAccess(true)
    renderPanel({ fullAccessByDefault: true })

    const box = await screen.findByRole('checkbox', { name: 'Accesso completo' })
    await waitFor(() => expect(box).toBeChecked())
    await userEvent.click(box)
    expect(box).not.toBeChecked()
  })

  it('never asks for what the space does not offer, even when preselected', async () => {
    spaceOffersFullAccess(false)
    vi.mocked(api.POST).mockReturnValueOnce(Promise.resolve(ok({ id: 't', token: 'pgc_x' })))
    renderPanel({ fullAccessByDefault: true })

    const box = await screen.findByRole('checkbox', { name: 'Accesso completo' })
    expect(box).toBeDisabled()
    expect(box).not.toBeChecked()
    expect(screen.getByText(/Chiuso per questo spazio/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Crea il token' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/tokens', {
        body: { nome: 'Claude Code', full_access: false },
      }),
    )
  })
})
