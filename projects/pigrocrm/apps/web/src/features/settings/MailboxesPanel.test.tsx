import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MailboxesPanel } from './MailboxesPanel'
import type { Mailbox } from './mailboxesQueries'
import { api } from '@/lib/api'
import { canSeeSettingsTab } from '@/lib/permissions'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

const GET = vi.mocked(api.GET)
const POST = vi.mocked(api.POST)
const PUT = vi.mocked(api.PUT)
const DELETE = vi.mocked(api.DELETE)

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}
function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const BASE: Mailbox = {
  id: 'm-1',
  provider: 'imap',
  address: 'fatture@acme.example',
  owner_user_id: null,
  space_owned: true,
  purposes: ['billing'],
  health: 'active',
  last_error: null,
  last_error_at: null,
  failing_since: null,
  host: 'imaps.acme.example',
  port: 993,
  security: 'tls',
  username: 'fatture',
}
const REFUSED: Mailbox = {
  ...BASE,
  id: 'm-2',
  address: 'pec@acme.example',
  health: 'auth_failed',
  last_error: 'password rifiutata',
  last_error_at: '2026-11-09T08:00:00Z',
}
const FAILING: Mailbox = {
  ...BASE,
  id: 'm-3',
  address: 'info@acme.example',
  purposes: ['correspondence'],
  health: 'failing',
  last_error: 'Il server non risponde.',
  failing_since: '2026-11-09T07:00:00Z',
}
const GMAIL: Mailbox = {
  ...BASE,
  id: 'm-4',
  provider: 'gmail',
  address: 'mario@gmail.example',
  space_owned: false,
  purposes: ['correspondence'],
  host: null,
  port: null,
  security: null,
  username: null,
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MailboxesPanel />
    </QueryClientProvider>,
  )
}

function row(id: string): HTMLElement {
  return document.querySelector(`[data-mailbox="${id}"]`) as HTMLElement
}

describe('MailboxesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    GET.mockResolvedValue(ok([BASE, REFUSED, FAILING, GMAIL]))
  })

  it('says each state in a word and Gmail points at its own tab', async () => {
    renderPanel()
    await screen.findByText('fatture@acme.example')
    expect(within(row('m-1')).getByText('Funziona')).toBeVisible()
    expect(within(row('m-2')).getByText('Password rifiutata')).toBeVisible()
    expect(within(row('m-3')).getByText('Non risponde')).toBeVisible()
    expect(within(row('m-3')).getByText(/Il server non risponde\./)).toBeVisible()
    expect(within(row('m-4')).getByRole('link', { name: /Gestisci .* in Gmail/ })).toHaveAttribute(
      'href',
      '/app/settings/gmail',
    )
    expect(within(row('m-4')).queryByRole('button')).toBeNull()
  })

  it('shows an empty state when nothing is connected', async () => {
    GET.mockResolvedValue(ok([]))
    renderPanel()
    expect(await screen.findByText(/Nessuna casella collegata/)).toBeVisible()
  })

  it('shows the error banner when the read fails', async () => {
    GET.mockResolvedValue(failed({ title: 'boom', detail: 'boom', status: 500 }, 500))
    renderPanel()
    expect(await screen.findByRole('alert')).toBeVisible()
  })

  it('connects a mailbox with what the API takes, and shows the API’s own refusal', async () => {
    const user = userEvent.setup()
    POST.mockResolvedValueOnce(
      failed({ title: 'x', detail: 'Il server non ha accettato la password.', status: 422 }, 422),
    )
    renderPanel()
    await screen.findByText('fatture@acme.example')

    const submit = screen.getByRole('button', { name: 'Collega la casella' })
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('Indirizzo della casella'), 'nuova@acme.example')
    await user.type(screen.getByLabelText('Server IMAP'), 'imaps.acme.example')
    await user.type(screen.getByLabelText('Utente'), 'nuova')
    await user.type(screen.getByLabelText('Password'), 's3greta')
    await user.click(submit)

    expect(POST).toHaveBeenCalledWith('/api/mailboxes/imap', {
      body: {
        address: 'nuova@acme.example',
        host: 'imaps.acme.example',
        port: 993,
        security: 'tls',
        username: 'nuova',
        password: 's3greta',
        purposes: ['correspondence'],
        space_owned: true,
      },
    })
    expect(await screen.findByText('Il server non ha accettato la password.')).toBeVisible()
  })

  it('replaces a refused password', async () => {
    const user = userEvent.setup()
    PUT.mockResolvedValueOnce(ok({ ...REFUSED, health: 'active' }))
    renderPanel()
    await screen.findByText('pec@acme.example')

    await user.click(within(row('m-2')).getByRole('button', { name: /Cambia la password/ }))
    await user.type(screen.getByLabelText(/Nuova password/), 'nuova-pw')
    await user.click(within(row('m-2')).getByRole('button', { name: 'Salva' }))

    expect(PUT).toHaveBeenCalledWith('/api/mailboxes/{mailbox_id}/password', {
      params: { path: { mailbox_id: 'm-2' } },
      body: { password: 'nuova-pw' },
    })
    await waitFor(() => expect(screen.queryByLabelText(/Nuova password/)).toBeNull())
  })

  it('removes a mailbox only after the confirmation', async () => {
    const user = userEvent.setup()
    DELETE.mockResolvedValueOnce(ok({ ...BASE, health: 'disconnected' }))
    renderPanel()
    await screen.findByText('fatture@acme.example')

    await user.click(within(row('m-1')).getByRole('button', { name: /Scollega fatture/ }))
    expect(DELETE).not.toHaveBeenCalled()
    const dialog = within(screen.getByRole('dialog'))
    await user.click(dialog.getByRole('button', { name: 'Scollega' }))

    expect(DELETE).toHaveBeenCalledWith('/api/mailboxes/{mailbox_id}', {
      params: { path: { mailbox_id: 'm-1' } },
    })
  })

  it('is an admin’s tab: no other role sees it', () => {
    expect(canSeeSettingsTab('admin', 'mailboxes')).toBe(true)
    expect(canSeeSettingsTab('collaborator', 'mailboxes')).toBe(false)
    expect(canSeeSettingsTab('readonly', 'mailboxes')).toBe(false)
  })
})
