import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from '@rebase/ui/sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConnectAgentDialog } from './ConnectAgentDialog'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
// `useCreateToken` (queries.ts) reads `useAuth()` for the query-invalidation key, the
// same as `TokensPanel`'s own test mocks it: this dialog needs no `AuthProvider` of its
// own, just a signed-in user for that hook to read.
vi.mock('@/lib/auth', () => ({ useAuth: vi.fn() }))
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useBlocker: vi.fn() }
})
const mockTenant = vi.hoisted(() => ({ prefix: '' }))
vi.mock('@/lib/tenant', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/tenant')>()
  return {
    ...actual,
    get tenantPrefix() {
      return mockTenant.prefix
    },
  }
})

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

// The jest-dom version installed here rejects an asymmetric matcher
// (`expect.stringContaining`) as `toHaveValue`'s argument for a `<textarea>`, even
// though the element's own value is the right one -- confirmed live, the failure
// message itself echoes the correct string back. Reading `.value` and asserting with
// `toContain` checks the identical thing without depending on that matcher overload.
function textareaValue(label: string): string {
  return (screen.getByLabelText(label) as HTMLTextAreaElement).value
}

function renderDialog(onOpenChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ConnectAgentDialog open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  )
  return onOpenChange
}

beforeEach(() => {
  vi.mocked(api.POST).mockReset()
  mockTenant.prefix = ''
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: 'u1',
      email: 'admin@pigro.it',
      nome: 'Admin',
      role: 'admin',
      active: true,
      default_hourly_rate: null,
      default_hourly_cost: null,
      created_at: '2026-09-12T10:00:00Z',
      last_login_at: null,
      weekly_digest: true,
    },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    enterWithLink: vi.fn(),
    enterWithInvite: vi.fn(),
    isError: false,
    retrySession: vi.fn(),
  })
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('ConnectAgentDialog', () => {
  it('shows the endpoint of this installation, built from the page origin and the space prefix', () => {
    mockTenant.prefix = '/studio'
    renderDialog()
    expect(screen.getByRole('dialog', { name: 'Collega un agente' })).toBeInTheDocument()
    expect(screen.getByLabelText('Endpoint')).toHaveValue(`${window.location.origin}/studio/mcp`)
  })

  it('carries a placeholder in both snippets until a token exists', () => {
    renderDialog()
    expect(textareaValue('Comando per Claude Code')).toContain('Bearer <token>')
    expect(textareaValue('Configurazione JSON')).toContain('Bearer <token>')
    expect(textareaValue('Comando per Claude Code')).toContain(
      `claude mcp add --transport http pigrocrm ${window.location.origin}/mcp`,
    )
  })

  it('mints a token with the prefilled name and puts it in both snippets', async () => {
    vi.mocked(api.POST).mockReturnValueOnce(
      Promise.resolve(
        ok({
          id: 't2',
          nome: 'Claude Code',
          prefix: 'pgc_zzzz9999',
          full_access: false,
          last_used_at: null,
          revoked_at: null,
          created_at: '2026-09-12T10:00:00Z',
          token: 'pgc_il-valore-intero',
        }),
      ),
    )
    renderDialog()
    expect(screen.getByLabelText('Nome del token')).toHaveValue('Claude Code')
    await userEvent.click(screen.getByRole('button', { name: 'Crea il token' }))
    expect(await screen.findByDisplayValue('pgc_il-valore-intero')).toBeInTheDocument()
    expect(textareaValue('Comando per Claude Code')).toContain('Bearer pgc_il-valore-intero')
    expect(textareaValue('Configurazione JSON')).toContain('Bearer pgc_il-valore-intero')
    expect(vi.mocked(api.POST)).toHaveBeenCalledWith('/api/tokens', { body: { nome: 'Claude Code', full_access: false } })
    // The name field and the button are gone: the token is minted exactly once per dialog.
    expect(screen.queryByRole('button', { name: 'Crea il token' })).not.toBeInTheDocument()
  })

  it('copies a snippet to the clipboard', async () => {
    renderDialog()
    await userEvent.click(screen.getByRole('button', { name: 'Copia il comando per Claude Code' }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(expect.stringContaining('claude mcp add'))
  })

  it('asks before closing while a token is on screen, and closes only on yes', async () => {
    vi.mocked(api.POST).mockReturnValueOnce(
      Promise.resolve(ok({ id: 't2', nome: 'Claude Code', prefix: 'pgc_zzzz9999', full_access: false, last_used_at: null, revoked_at: null, created_at: '2026-09-12T10:00:00Z', token: 'pgc_x' })),
    )
    const onOpenChange = renderDialog()
    await userEvent.click(screen.getByRole('button', { name: 'Crea il token' }))
    await screen.findByDisplayValue('pgc_x')
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    await userEvent.click(screen.getByRole('button', { name: 'Chiudi' }))
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    vi.mocked(window.confirm).mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: 'Chiudi' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('has no link out: the page it opens on is the token list', () => {
    renderDialog()
    expect(within(screen.getByRole('dialog')).queryByRole('link')).not.toBeInTheDocument()
  })

  it('says so when the clipboard refuses', async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    renderDialog()
    await userEvent.click(screen.getByRole('button', { name: "Copia l'endpoint" }))
    await vi.waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Copia negli appunti non riuscita'),
    )
  })
})
