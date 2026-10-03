import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EmitterPanel } from './EmitterPanel'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const PROFILE = {
  id: 'e-1',
  ragione_sociale: 'Studio Rossi',
  partita_iva: '12345678901',
  codice_fiscale: null,
  indirizzo: 'Via Roma 1',
  cap: '20100',
  comune: 'Milano',
  provincia: 'MI',
  nazione: 'IT',
  pec: 'studio@pec.it',
  codice_sdi: null,
  telefono: null,
  email: null,
  sito_web: null,
  logo_key: null,
  firma_key: null,
  regime_fiscale: 'RF19',
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <EmitterPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PUT).mockReset()
})

describe('EmitterPanel', () => {
  it('seeds the form from the stored profile', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(PROFILE))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText(/Ragione sociale/)).toHaveValue('Studio Rossi'))
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('12345678901')
    // A stored `null` becomes an empty input, never the string "null".
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('')
  })

  /**
   * A fresh install has no row and `GET /api/emitter` answers 404. That is not an
   * error the operator needs to see -- it means "not configured yet" -- so it must
   * read as an empty state and never as a failure.
   */
  it('reads a 404 as not-yet-configured, not as an error', async () => {
    vi.mocked(api.GET).mockResolvedValue(failed({ detail: 'not found' }, 404))
    renderPanel()

    expect(
      await screen.findByText('Profilo non ancora configurato: compila i campi e salva.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Nazione')).toHaveValue('IT')
  })

  /**
   * The alert, and *nothing else*. A 404 means "not configured yet" and gets the form;
   * anything else means the row may exist and simply could not be read, and a blank
   * form in that state invites somebody to fill it in and save -- a PUT of every key,
   * overwriting a profile the panel never managed to show them.
   */
  it('shows any other failure as an alert, and hides the form behind it', async () => {
    vi.mocked(api.GET).mockResolvedValue(failed({ detail: 'database non raggiungibile' }, 503))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByLabelText(/Ragione sociale/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Salva' })).not.toBeInTheDocument()
  })

  /**
   * Clearing a field must send `''`, not omit the key: omitting it would leave the
   * old value stored while the screen showed the field as empty -- the same
   * failure the entity forms already had to fix.
   */
  it('sends an emptied field as an empty string rather than omitting it', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(PROFILE))
    vi.mocked(api.PUT).mockResolvedValue(ok({ ...PROFILE, pec: '' }))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('PEC')).toHaveValue('studio@pec.it'))
    await userEvent.clear(screen.getByLabelText('PEC'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    const [, options] = vi.mocked(api.PUT).mock.calls[0] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body).toHaveProperty('pec', '')
  })

  it('attaches the server message to the field it names', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(PROFILE))
    vi.mocked(api.PUT).mockResolvedValue(
      failed(
        {
          code: 'validation_failed',
          entity: 'emitter_profile',
          field: 'partita_iva',
          reason: 'deve essere di 11 cifre',
          expected: '11 cifre numeriche',
          detail: 'deve essere di 11 cifre',
        },
        422,
      ),
    )
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Partita IVA')).toHaveValue('12345678901'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(
      await screen.findByText('deve essere di 11 cifre (atteso: 11 cifre numeriche)'),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Partita IVA')).toHaveAttribute('aria-invalid', 'true')
  })

  /**
   * `logo_key` and `firma_key` are storage keys for images with no upload UI yet.
   * Offering a raw key field would invite someone to type a path that resolves to
   * nothing, so the panel deliberately does not render them.
   */
  it('does not offer the two image storage keys as free-text fields', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(PROFILE))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText(/Ragione sociale/)).toHaveValue('Studio Rossi'))
    expect(screen.queryByLabelText(/logo/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/firma/i)).not.toBeInTheDocument()
  })
})
