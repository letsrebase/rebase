import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GmailPanel } from './GmailPanel'
import type { GmailHealth } from './queries'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
  }
})

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const READONLY = 'https://www.googleapis.com/auth/gmail.readonly'
const SEND = 'https://www.googleapis.com/auth/gmail.send'

const ACCOUNT = {
  id: '00000000-0000-7000-8000-000000000001',
  email_address: 'ada@acme.it',
  scopes_granted: ['openid', 'https://www.googleapis.com/auth/userinfo.email', READONLY, SEND],
  status: 'active' as const,
  consent_expires_at: null,
  last_error: null,
  last_error_at: null,
  last_sync_at: null,
  sync_watermark: null,
  gmail_store_bodies: true,
  connected_at: '2026-08-01T09:00:00Z',
  disconnected_at: null,
}

const CONNECTED: GmailHealth = {
  account: ACCOUNT,
  banner: null,
  banner_text: null,
  missing_scopes: [],
  configured: true,
}

const NOT_CONFIGURED: GmailHealth = {
  account: null,
  banner: null,
  banner_text: null,
  missing_scopes: [],
  configured: false,
}

const NOT_CONNECTED: GmailHealth = { ...NOT_CONFIGURED, configured: true }

const EMPTY_REPORT = {
  started_at: '2026-08-20T09:30:00Z',
  already_running: false,
  running_since: null,
  queries_issued: 1,
  threads_fetched: 2,
  messages_stored: 3,
  messages_skipped: 0,
  links_created: 4,
  states_pruned: 0,
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <GmailPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

describe('GmailPanel', () => {
  it('shows the connected mailbox, the granted scopes and the last sync', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    renderPanel()

    expect(await screen.findByText('ada@acme.it')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sincronizza adesso' })).toBeEnabled()
    expect(screen.getByText(/gmail\.readonly/)).toBeInTheDocument()
    // `last_sync_at: null` is "mai", never a blank or an Invalid Date -- the one
    // formatted value that can be asserted without depending on the runner's timezone.
    expect(screen.getByText(/Ultimo sync: mai/)).toBeInTheDocument()
  })

  /**
   * Absent, not broken. An installation with no Google client deliberately has no
   * Gmail, and the panel says so without offering a button that would answer 409.
   */
  it('says Gmail is not available on this installation rather than showing a broken panel', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(NOT_CONFIGURED))
    renderPanel()

    expect(
      await screen.findByText(/non è configurato su questa installazione/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sincronizza adesso' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Collega/ })).not.toBeInTheDocument()
  })

  /**
   * The other half of the same response, and the reason `configured` exists at all: a
   * configured installation nobody has consented on yet is one button away from
   * working, and telling that person "Gmail non è configurato" sends them to look for
   * an environment variable that is already set.
   */
  it('offers the consent flow when Gmail is configured but no mailbox is connected', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(NOT_CONNECTED))
    renderPanel()

    expect(await screen.findByRole('link', { name: 'Collega la casella Google' })).toHaveAttribute(
      'href',
      '/api/gmail/oauth/start',
    )
    expect(screen.queryByText(/non è configurato su questa installazione/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sincronizza adesso' })).not.toBeInTheDocument()
  })

  /**
   * A failed read must not render controls under it. The panel writes back through
   * PATCH and DELETE, and a checkbox rendered from a health response that never
   * arrived would offer to change a setting nobody managed to show the user -- the
   * same defect the emitter panel was fixed for.
   */
  it('renders the error banner instead of an empty panel when the request failed', async () => {
    vi.mocked(api.GET).mockResolvedValue(failed({ detail: 'database non raggiungibile' }, 503))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByText(/non è configurato/)).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Scollega la casella' })).not.toBeInTheDocument()
  })

  it('disables the sync button and explains why when readonly was not granted', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...CONNECTED,
        account: { ...ACCOUNT, scopes_granted: ['openid', 'https://www.googleapis.com/auth/userinfo.email', SEND] },
        banner: 'scope_missing',
        banner_text: `Il sync è spento: manca l'autorizzazione ${READONLY}.`,
        missing_scopes: [READONLY],
      }),
    )
    renderPanel()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Sincronizza adesso' })).toBeDisabled(),
    )
    expect(screen.getByText(/manca l'autorizzazione/)).toBeInTheDocument()
    // Re-authorising is the cure, so the way to it stays available.
    expect(screen.getByRole('link', { name: 'Ri-autorizza' })).toBeInTheDocument()
  })

  it('disables the sync button when the credential was revoked', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...CONNECTED,
        account: { ...ACCOUNT, status: 'revoked' as const, last_error: 'consenso revocato' },
        banner: 'revoked',
        banner_text: 'Il consenso Google per ada@acme.it è stato revocato: ricollega la casella.',
      }),
    )
    renderPanel()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Sincronizza adesso' })).toBeDisabled(),
    )
    expect(screen.getByText(/è stato revocato/)).toBeInTheDocument()
  })

  it('makes the disconnect ask about the stored messages, defaulting to keeping them', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.DELETE).mockResolvedValue(ok(undefined))
    renderPanel()

    const checkbox = await screen.findByRole('checkbox', { name: /Elimina anche le email/i })
    // Never the default: deleting a customer's correspondence because a token expired
    // would be a disaster.
    expect(checkbox).not.toBeChecked()

    await userEvent.click(screen.getByRole('button', { name: 'Scollega la casella' }))
    await waitFor(() => expect(api.DELETE).toHaveBeenCalled())
    expect(vi.mocked(api.DELETE).mock.calls[0]?.[1]).toMatchObject({
      params: { query: { elimina_messaggi: false } },
    })
  })

  it('sends the deletion choice when it is actually made', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.DELETE).mockResolvedValue(ok(undefined))
    renderPanel()

    await userEvent.click(await screen.findByRole('checkbox', { name: /Elimina anche le email/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Scollega la casella' }))

    await waitFor(() => expect(api.DELETE).toHaveBeenCalled())
    expect(vi.mocked(api.DELETE).mock.calls[0]?.[1]).toMatchObject({
      params: { query: { elimina_messaggi: true } },
    })
  })

  it('switches the body store off through the API', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.PATCH).mockResolvedValue(ok({ ...ACCOUNT, gmail_store_bodies: false }))
    renderPanel()

    await userEvent.click(await screen.findByRole('checkbox', { name: /Conserva il testo/i }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(vi.mocked(api.PATCH).mock.calls[0]?.[1]).toMatchObject({
      body: { gmail_store_bodies: false },
    })
  })

  /**
   * The stale-banner defect, in the one place it can still happen: a failed action.
   * The message must be the server's own, and it must be gone on the next attempt --
   * which is why it is derived from the mutation's state rather than accumulated in
   * component state, where nothing would ever clear it.
   */
  it('shows why a sync failed, and stops showing it once one succeeds', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.POST).mockResolvedValue(
      failed({ code: 'conflict', detail: 'Gmail non ha risposto correttamente' }, 409),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Sincronizza adesso' }))
    expect(await screen.findByText(/Gmail non ha risposto correttamente/)).toBeInTheDocument()

    vi.mocked(api.POST).mockResolvedValue(ok(EMPTY_REPORT))
    await userEvent.click(screen.getByRole('button', { name: 'Sincronizza adesso' }))

    await waitFor(() =>
      expect(screen.queryByText(/Gmail non ha risposto correttamente/)).not.toBeInTheDocument(),
    )
  })

  it('reports what a finished cycle actually did', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.POST).mockResolvedValue(ok(EMPTY_REPORT))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Sincronizza adesso' }))

    expect(await screen.findByText(/3 email archiviate/)).toBeInTheDocument()
  })

  /**
   * `already_running` is a report, not an error (B1-9): a cron every fifteen minutes
   * and somebody pressing the button is not a hypothetical, and the second caller is
   * told what is happening rather than shown a failure.
   */
  it('says a cycle is already running instead of reporting zero', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.POST).mockResolvedValue(
      ok({ ...EMPTY_REPORT, already_running: true, messages_stored: 0 }),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Sincronizza adesso' }))

    expect(await screen.findByText(/già in corso/)).toBeInTheDocument()
    expect(screen.queryByText(/0 email archiviate/)).not.toBeInTheDocument()
  })
})
