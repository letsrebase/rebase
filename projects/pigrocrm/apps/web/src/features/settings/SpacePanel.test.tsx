/**
 * Impostazioni → Spazio, driven as an admin drives it. The API is stubbed at
 * `api.GET`/`api.PUT`, the same seam the other settings panels use.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { components } from '@/lib/api-types'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn() } }
})

import { api } from '@/lib/api'
import { SpacePanel } from './SpacePanel'
import { changesBetween, draftFrom } from './spaceChanges'

const GET = api.GET as unknown as ReturnType<typeof vi.fn>
const PUT = api.PUT as unknown as ReturnType<typeof vi.fn>

const SETTINGS: components['schemas']['SpaceSettingsRead'] = {
  spazio: 'studio',
  public_url: 'https://pigro.example/studio',
  google_client_id: '',
  google_client_secret_impostato: false,
  google_token_key_impostata: false,
  google_app_unverified: false,
  gmail_configurato: false,
  google_client_condiviso: false,
  redirect_uri_gmail: 'https://pigro.example/studio/api/gmail/oauth/callback',
  redirect_uri_drive: 'https://pigro.example/studio/api/drive/oauth/callback',
  storage_backend: 'local',
  mcp_full_access: false,
  solleciti_grace_days: 7,
  solleciti_min_interval_days: 14,
  solleciti_max_reminders: 3,
  gmail_backfill_days: 90,
  concentrazione_soglia_preferita: 0.3,
  sovrascritte: [],
  updated_at: null,
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SpacePanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  GET.mockReset()
  PUT.mockReset()
  GET.mockResolvedValue({ data: SETTINGS })
})

describe('changesBetween', () => {
  it('sends only what differs, and a secret only when typed', () => {
    const draft = {
      google_client_id: 'abc.apps',
      google_client_secret: '',
      google_app_unverified: false,
      storage_backend: 'local' as const,
      mcp_full_access: true,
      gmail_backfill_days: '120',
    }
    expect(changesBetween(SETTINGS, draft)).toEqual({
      google_client_id: 'abc.apps',
      mcp_full_access: true,
      gmail_backfill_days: 120,
    })
  })
})

describe('changesBetween, while the space borrows the platform client', () => {
  const BORROWING = {
    ...SETTINGS,
    google_client_id: 'platform.apps',
    google_client_secret_impostato: true,
    google_app_unverified: true,
    gmail_configurato: true,
    google_client_condiviso: true,
  }

  it('declares Testing in the same save that brings a client of the space’s own', () => {
    const draft = { ...draftFrom(BORROWING), google_client_id: 'own.apps', google_client_secret: 's' }
    expect(draft.google_app_unverified).toBe(true)
    expect(changesBetween(BORROWING, draft)).toEqual({
      google_client_id: 'own.apps',
      google_client_secret: 's',
      google_app_unverified: true,
    })
  })

  it('sends no Testing row for the borrowed client itself', () => {
    const draft = { ...draftFrom(BORROWING), mcp_full_access: true }
    expect(changesBetween(BORROWING, draft)).toEqual({ mcp_full_access: true })
  })
})

describe('the space panel', () => {
  it('names the space, shows the redirect URIs and where each value comes from', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByText('Spazio studio')).toBeInTheDocument())
    expect(screen.getByText('https://pigro.example/studio/api/gmail/oauth/callback')).toBeInTheDocument()
    expect(screen.getAllByText("dall'ambiente").length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
  })

  it('says the platform lends its client and keeps the space\'s own redirects for a client of its own', async () => {
    GET.mockResolvedValue({
      data: {
        ...SETTINGS,
        google_client_id: 'platform.apps',
        google_client_secret_impostato: true,
        google_token_key_impostata: true,
        gmail_configurato: true,
        google_client_condiviso: true,
      },
    })
    renderPanel()
    await waitFor(() =>
      expect(screen.getByText(/usa il client Google della piattaforma/)).toBeInTheDocument(),
    )
    // The space's own addresses stay, for whoever wants a client of their own.
    expect(screen.getByText('Solo per un client tuo: i redirect URI da registrare su Google')).toBeInTheDocument()
    expect(screen.getByText('https://pigro.example/studio/api/gmail/oauth/callback')).toBeInTheDocument()
    // Whether the platform's client is verified is the platform's to say, until the
    // person brings a client of their own.
    expect(screen.queryByLabelText(/ancora in "Testing"/)).not.toBeInTheDocument()
    const user = userEvent.setup()
    const clientId = screen.getByLabelText('Client ID')
    await user.clear(clientId)
    await user.type(clientId, 'own.apps')
    expect(screen.getByLabelText(/ancora in "Testing"/)).toBeChecked()
  })

  it('saves the changed keys and adopts the answer', async () => {
    PUT.mockResolvedValue({
      data: { ...SETTINGS, gmail_backfill_days: 21, sovrascritte: ['gmail_backfill_days'] },
    })
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('Spazio studio')).toBeInTheDocument())
    const backfill = screen.getByLabelText('Giorni di posta al primo collegamento')
    await user.clear(backfill)
    await user.type(backfill, '21')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    // The version it read travels with the changed keys (REB-622): `null` on a
    // database with no override row yet, and sent as such.
    expect(PUT).toHaveBeenCalledWith('/api/settings/space', {
      body: { gmail_backfill_days: 21, updated_at: null },
    })
    await waitFor(() => expect(screen.getAllByText('impostato qui').length).toBe(1))
  })

  it('keeps the version it was seeded from under an open draft, and a failed reload keeps the refusal', async () => {
    const theirs = { ...SETTINGS, mcp_full_access: true, updated_at: '2026-08-21T09:00:00Z' }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    GET.mockResolvedValueOnce({ data: SETTINGS }).mockResolvedValueOnce({ data: theirs })
    PUT.mockResolvedValue({
      error: { code: 'stale_row', detail: 'qualcun altro ha salvato nel frattempo: ricarica e riprova' },
      response: new Response(null, { status: 409 }),
    })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={client}>
        <SpacePanel />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByText('Spazio studio')).toBeInTheDocument())
    const backfill = screen.getByLabelText('Giorni di posta al primo collegamento')
    await user.clear(backfill)
    await user.type(backfill, '21')
    // A background refetch brings the other admin's save under the draft: their switch
    // shows, the version this form sends stays the one it read.
    await client.invalidateQueries({ queryKey: ['settings', 'space'] })
    await waitFor(() => expect(screen.getByLabelText(/Accesso completo/)).toBeChecked())
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    expect(PUT).toHaveBeenLastCalledWith('/api/settings/space', {
      body: { gmail_backfill_days: 21, updated_at: null },
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('Qualcun altro ha salvato nel frattempo.')
    // The reload fails: nothing was reloaded, so nothing says it was. The error shows
    // above the form, which keeps the draft and the refusal with its «Ricarica», so
    // the person can try again from here.
    GET.mockResolvedValueOnce({ error: { detail: 'database non raggiungibile' }, response: new Response(null, { status: 503 }) })
    await user.click(screen.getByRole('button', { name: 'Ricarica' }))
    await waitFor(() => expect(screen.getByText('database non raggiungibile')).toBeInTheDocument())
    expect(screen.queryByText(/Riga ricaricata/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Giorni di posta al primo collegamento')).toHaveValue(21)
    expect(screen.getByText(/Qualcun altro ha salvato nel frattempo/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ricarica' })).toBeEnabled()
    // And the next reload that answers brings their row under the edits.
    GET.mockResolvedValue({ data: theirs })
    await user.click(screen.getByRole('button', { name: 'Ricarica' }))
    await waitFor(() => expect(screen.getByText(/Riga ricaricata/)).toBeInTheDocument())
    expect(screen.queryByText('database non raggiungibile')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Giorni di posta al primo collegamento')).toHaveValue(21)
    expect(screen.getByLabelText(/Accesso completo/)).toBeChecked()
  })

  it('locks the fields while a save is in flight, so nothing typed then is lost', async () => {
    let finish!: (value: unknown) => void
    PUT.mockReturnValue(new Promise((resolve) => (finish = resolve)))
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('Spazio studio')).toBeInTheDocument())
    const backfill = screen.getByLabelText('Giorni di posta al primo collegamento')
    await user.clear(backfill)
    await user.type(backfill, '21')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByLabelText('Giorni di posta al primo collegamento')).toBeDisabled())
    expect(screen.getByLabelText(/Accesso completo/)).toBeDisabled()
    finish({ data: { ...SETTINGS, gmail_backfill_days: 21, updated_at: '2026-08-21T09:00:00Z' } })
    await waitFor(() => expect(screen.getByLabelText('Giorni di posta al primo collegamento')).toBeEnabled())
  })

  it('on a stale refusal offers Ricarica, which keeps the edits over the reloaded settings', async () => {
    const theirs = {
      ...SETTINGS,
      mcp_full_access: true,
      sovrascritte: ['mcp_full_access'],
      updated_at: '2026-08-21T09:00:00Z',
    }
    GET.mockResolvedValueOnce({ data: SETTINGS }).mockResolvedValue({ data: theirs })
    PUT.mockResolvedValueOnce({
      error: {
        type: 'https://pigrocrm.dev/errors/stale_row',
        title: 'Riga cambiata nel frattempo',
        status: 409,
        detail: 'qualcun altro ha salvato nel frattempo: ricarica e riprova',
        code: 'stale_row',
        entity: 'space_settings',
        updated_at: '2026-08-21T09:00:00Z',
      },
      response: new Response(null, { status: 409 }),
    }).mockResolvedValue({
      data: {
        ...theirs,
        gmail_backfill_days: 21,
        sovrascritte: ['gmail_backfill_days', 'mcp_full_access'],
        updated_at: '2026-08-22T09:00:00Z',
      },
    })
    const user = userEvent.setup()
    renderPanel()
    await waitFor(() => expect(screen.getByText('Spazio studio')).toBeInTheDocument())
    const backfill = screen.getByLabelText('Giorni di posta al primo collegamento')
    await user.clear(backfill)
    await user.type(backfill, '21')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Qualcun altro ha salvato nel frattempo.')
    expect(screen.getByLabelText('Giorni di posta al primo collegamento')).toHaveValue(21)

    await user.click(screen.getByRole('button', { name: 'Ricarica' }))
    // The other admin's switch shows, the edit stays, and the banner says so.
    await waitFor(() => expect(screen.getByLabelText(/Accesso completo/)).toBeChecked())
    expect(screen.getByLabelText('Giorni di posta al primo collegamento')).toHaveValue(21)
    expect(screen.getByRole('alert')).toHaveTextContent('Riga ricaricata.')

    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(PUT).toHaveBeenCalledTimes(2))
    expect(PUT).toHaveBeenLastCalledWith('/api/settings/space', {
      body: { gmail_backfill_days: 21, updated_at: '2026-08-21T09:00:00Z' },
    })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
