import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { PointerEventsCheckLevel } from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DrivePanel } from './DrivePanel'
import type { DriveHealth } from './queries'
import { api } from '@/lib/api'

// REB-457: the panel is open to a collaboratore too, who manages their own Drive but not
// the space's write folder. Every test below runs as an admin unless it says otherwise.
const mockAuth = vi.hoisted(() => ({ isAdmin: true }))
vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => mockAuth.isAdmin,
  useCanWrite: () => true,
}))

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

const READONLY = 'https://www.googleapis.com/auth/drive.readonly'
const FILE = 'https://www.googleapis.com/auth/drive.file'

const ROOT_ID_1 = 'AAAAAAAAAAAAAAAAAAAA'
const ROOT_ID_2 = 'BBBBBBBBBBBBBBBBBBBB'
const STORAGE_ID = 'CCCCCCCCCCCCCCCCCCCC'

const ACCOUNT = {
  id: '00000000-0000-7000-8000-000000000001',
  email_address: 'ada@acme.it',
  scopes_granted: ['openid', 'email', READONLY, FILE],
  status: 'active' as const,
  consent_expires_at: null,
  root_folder_ids: [ROOT_ID_1],
  storage_folder_id: STORAGE_ID,
  storage_folder_verified: true,
  last_error: null,
  last_error_at: null,
  connected_at: '2026-08-01T09:00:00Z',
  disconnected_at: null,
}

const CONNECTED: DriveHealth = {
  account: ACCOUNT,
  banner: null,
  banner_text: null,
  missing_scopes: [],
  configured: true,
}

const NOT_CONFIGURED: DriveHealth = {
  account: null,
  banner: null,
  banner_text: null,
  missing_scopes: [],
  configured: false,
}

const NOT_CONNECTED: DriveHealth = { ...NOT_CONFIGURED, configured: true }

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <DrivePanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockAuth.isAdmin = true
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

describe('DrivePanel', () => {
  it('shows the connected account, its status and the configured roots', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    renderPanel()

    expect(await screen.findByText('ada@acme.it')).toBeInTheDocument()
    expect(screen.getByText(/attiva/)).toBeInTheDocument()
    expect(screen.getByDisplayValue(ROOT_ID_1)).toBeInTheDocument()
    expect(screen.getByDisplayValue(STORAGE_ID)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scollega Drive' })).toBeInTheDocument()
  })

  it('says Drive is not available on this installation rather than showing a broken panel', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(NOT_CONFIGURED))
    renderPanel()

    expect(
      await screen.findByText(/non è configurato su questa installazione/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Collega/ })).not.toBeInTheDocument()
  })

  it('offers the consent flow when Drive is configured but no account is connected', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(NOT_CONNECTED))
    renderPanel()

    expect(
      await screen.findByRole('link', { name: 'Collega Google Drive' }),
    ).toHaveAttribute('href', '/api/drive/oauth/start')
    expect(screen.queryByText(/non è configurato su questa installazione/)).not.toBeInTheDocument()
    // An admin also chooses the space's write folder, and is told so up front.
    expect(screen.getByText(/scrivere i documenti generati/)).toBeInTheDocument()
  })

  it('renders the error banner instead of an empty panel when the request failed', async () => {
    vi.mocked(api.GET).mockResolvedValue(failed({ detail: 'database non raggiungibile' }, 503))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByRole('button', { name: 'Scollega Drive' })).not.toBeInTheDocument()
  })

  it('shows the banner above the roots form, not instead of it', async () => {
    // Hiding the editor whenever `banner_text` is set was wrong on both counts. A
    // revoked or expired consent is precisely the state `set_roots` admits on purpose
    // -- the server lets that credential be reconfigured because the person on this
    // screen is the one recovering from it -- and `expiring` is a *working* credential
    // with a date attached, where removing the editor takes a control away over a
    // warning about next week.
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...CONNECTED,
        account: { ...ACCOUNT, status: 'revoked' as const },
        banner: 'revoked',
        banner_text: 'Il consenso Google per ada@acme.it è stato revocato: ricollega Drive.',
      }),
    )
    renderPanel()

    expect(await screen.findByText(/è stato revocato/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Aggiungi cartella' })).toBeInTheDocument()
    expect(screen.getByDisplayValue(ROOT_ID_1)).toBeInTheDocument()
    // Disconnecting stays available even with a broken credential.
    expect(screen.getByRole('button', { name: 'Scollega Drive' })).toBeInTheDocument()
    // The banner comes first in document order: it says what to do about the
    // credential, and the editor below it is the recovery work it enables.
    const banner = screen.getByRole('status')
    const editor = screen.getByDisplayValue(ROOT_ID_1)
    expect(banner.compareDocumentPosition(editor) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('keeps the roots form for an expiring consent, which is a working credential', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...CONNECTED,
        banner: 'expiring',
        banner_text: 'Il consenso Google Drive per ada@acme.it va rinnovato entro il 10/09/2026.',
      }),
    )
    renderPanel()

    expect(await screen.findByText(/va rinnovato/)).toBeInTheDocument()
    expect(screen.getByDisplayValue(ROOT_ID_1)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Salva' })).toBeEnabled()
  })

  it('asks for confirmation before disconnecting, and only calls DELETE once confirmed', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.DELETE).mockResolvedValue(ok(undefined))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Scollega Drive' }))
    expect(screen.getByText('Scollegare Google Drive?')).toBeInTheDocument()
    expect(screen.getByText(/cartella di archiviazione/)).toBeInTheDocument()
    expect(api.DELETE).not.toHaveBeenCalled()

    // Radix marks `document.body` `pointer-events: none` while the dialog is open (its
    // own scroll lock, restored on close) -- real browsers still resolve the click
    // because the content itself opts back in, but jsdom's computed style does not
    // reflect that override, so the click-time check is disabled for this one click.
    const user = userEvent.setup({ pointerEventsCheck: PointerEventsCheckLevel.Never })
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Scollega Drive' }))
    await waitFor(() => expect(api.DELETE).toHaveBeenCalledWith('/api/drive/account'))
  })

  it('cancels without calling DELETE', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Scollega Drive' }))
    await userEvent.click(screen.getByRole('button', { name: 'Annulla' }))

    expect(screen.queryByText('Scollegare Google Drive?')).not.toBeInTheDocument()
    expect(api.DELETE).not.toHaveBeenCalled()
  })

  it('rejects a malformed folder id client-side and disables Salva', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    renderPanel()

    const input = await screen.findByDisplayValue(ROOT_ID_1)
    await userEvent.clear(input)
    await userEvent.type(input, 'short')

    expect(await screen.findByText(/L'ID non è valido/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('adds a folder row, saves the roots and shows a success toast', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.PATCH).mockResolvedValue(
      ok({ ...ACCOUNT, root_folder_ids: [ROOT_ID_1, ROOT_ID_2] }),
    )
    renderPanel()

    await screen.findByDisplayValue(ROOT_ID_1)
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi cartella' }))
    const idInputs = screen.getAllByPlaceholderText('ID cartella Drive')
    await userEvent.type(idInputs[1]!, ROOT_ID_2)

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(vi.mocked(api.PATCH).mock.calls[0]?.[1]).toMatchObject({
      body: { root_folder_ids: [ROOT_ID_1, ROOT_ID_2], storage_folder_id: STORAGE_ID },
    })
  })

  it('sends storage_folder_id as null when the field is cleared', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.PATCH).mockResolvedValue(ok(ACCOUNT))
    renderPanel()

    const storageInput = await screen.findByDisplayValue(STORAGE_ID)
    await userEvent.clear(storageInput)
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(vi.mocked(api.PATCH).mock.calls[0]?.[1]).toMatchObject({
      body: { root_folder_ids: [ROOT_ID_1], storage_folder_id: null },
    })
  })

  it('shows why saving the roots failed', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.PATCH).mockResolvedValue(
      failed({ code: 'validation_failed', detail: 'ID cartella non valido' }, 422),
    )
    renderPanel()

    await screen.findByDisplayValue(ROOT_ID_1)
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(await screen.findByText('ID cartella non valido')).toBeInTheDocument()
  })
})

/**
 * REB-457. `DriveRepository.storage_account` picks the space's one write folder from
 * every user's row (the most recently updated `active` row that names one), so a folder
 * a collaboratore chose would receive every document the space generates. That field
 * stays an admin's; the rest of the panel (connect, the read roots, disconnect) is the
 * collaboratore's own credential and works for them as it does for an admin.
 */
describe('DrivePanel for a collaboratore', () => {
  beforeEach(() => {
    mockAuth.isAdmin = false
  })

  it('shows their own account and read roots, and no write folder field', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    renderPanel()

    expect(await screen.findByText('ada@acme.it')).toBeInTheDocument()
    expect(screen.getByDisplayValue(ROOT_ID_1)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Aggiungi cartella' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Salva' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Scollega Drive' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Cartella di scrittura')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue(STORAGE_ID)).not.toBeInTheDocument()
    expect(screen.getByText(/la sceglie un amministratore/)).toBeInTheDocument()
  })

  it('saves the read roots without naming the write folder, so the server leaves it alone', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.PATCH).mockResolvedValue(
      ok({ ...ACCOUNT, root_folder_ids: [ROOT_ID_1, ROOT_ID_2] }),
    )
    renderPanel()

    await screen.findByDisplayValue(ROOT_ID_1)
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi cartella' }))
    const idInputs = screen.getAllByPlaceholderText('ID cartella Drive')
    await userEvent.type(idInputs[1]!, ROOT_ID_2)
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    const body = (vi.mocked(api.PATCH).mock.calls[0]?.[1] as unknown as { body: object }).body
    expect(body).toEqual({ root_folder_ids: [ROOT_ID_1, ROOT_ID_2] })
    expect(body).not.toHaveProperty('storage_folder_id')
  })

  it('offers the consent flow without promising a write folder', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(NOT_CONNECTED))
    renderPanel()

    expect(
      await screen.findByRole('link', { name: 'Collega Google Drive' }),
    ).toHaveAttribute('href', '/api/drive/oauth/start')
    expect(screen.getByText(/quali cartelle la CRM può leggere/)).toBeInTheDocument()
    expect(screen.queryByText(/scrivere i documenti generati/)).not.toBeInTheDocument()
  })

  it('disconnects their own Drive, and the confirmation names no write folder', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(CONNECTED))
    vi.mocked(api.DELETE).mockResolvedValue(ok(undefined))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Scollega Drive' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).queryByText(/cartella di archiviazione/)).not.toBeInTheDocument()

    const user = userEvent.setup({ pointerEventsCheck: PointerEventsCheckLevel.Never })
    await user.click(within(dialog).getByRole('button', { name: 'Scollega Drive' }))
    await waitFor(() => expect(api.DELETE).toHaveBeenCalledWith('/api/drive/account'))
  })
})
