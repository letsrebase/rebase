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
import { changesBetween } from './spaceChanges'

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
    // Whether the platform's client is verified is the platform's to say.
    expect(screen.queryByLabelText(/ancora in "Testing"/)).not.toBeInTheDocument()
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
    expect(PUT).toHaveBeenCalledWith('/api/settings/space', { body: { gmail_backfill_days: 21 } })
    await waitFor(() => expect(screen.getAllByText('impostato qui').length).toBe(1))
  })
})
