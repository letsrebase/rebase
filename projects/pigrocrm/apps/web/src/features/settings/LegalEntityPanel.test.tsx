import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LegalEntityPanel } from './LegalEntityPanel'
import type { LegalEntityRecord } from './queries'
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

const AZIENDA: LegalEntityRecord = {
  id: 'a-1',
  nome: 'Studio Rossi',
  predefinita: true,
  attiva: true,
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
  firma_email: null,
  regime_fiscale: 'RF19',
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

function renderPanel(azienda: LegalEntityRecord = AZIENDA) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = (record: LegalEntityRecord) => (
    <QueryClientProvider client={client}>
      <LegalEntityPanel azienda={record} />
    </QueryClientProvider>
  )
  const result = render(tree(azienda))
  return { ...result, rerenderWith: (record: LegalEntityRecord) => result.rerender(tree(record)) }
}

function saveCall() {
  return vi.mocked(api.PUT).mock.calls[0] as unknown as [
    string,
    { params: { path: { azienda_id: string } }; body: Record<string, unknown> },
  ]
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PUT).mockReset()
})

describe('LegalEntityPanel', () => {
  it('seeds the form from the azienda it is given, short name included', () => {
    renderPanel()
    expect(screen.getByLabelText(/Ragione sociale/)).toHaveValue('Studio Rossi')
    expect(screen.getByLabelText('Nome breve')).toHaveValue('Studio Rossi')
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('12345678901')
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('')
  })

  /**
   * A fresh space shows the name it signed up with and empty fiscal fields: since
   * REB-615 the row always exists, so there is no «not configured yet» state and no
   * read of its own here; the page that owns the list hands the row down.
   */
  it('shows a fresh azienda with its name and the Italian default nation', () => {
    renderPanel({ ...AZIENDA, partita_iva: null, indirizzo: null, pec: null, regime_fiscale: null })
    expect(screen.getByLabelText(/Ragione sociale/)).toHaveValue('Studio Rossi')
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('')
    expect(screen.getByLabelText('Nazione')).toHaveValue('IT')
  })

  it('saves to the azienda by id and sends an emptied field as an empty string', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok({ ...AZIENDA, pec: '' }))
    renderPanel()
    await userEvent.clear(screen.getByLabelText('PEC'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    const [path, options] = saveCall()
    expect(path).toBe('/api/aziende/{azienda_id}')
    expect(options.params.path.azienda_id).toBe('a-1')
    expect(options.body.pec).toBe('')
    expect(options.body.nome).toBe('Studio Rossi')
  })

  it('follows a newer row while untouched and keeps a draft once typing started', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok(AZIENDA))
    const { rerenderWith } = renderPanel()
    // Another admin saved: the refetched row is newer and the form has no draft.
    rerenderWith({ ...AZIENDA, comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    expect(screen.getByLabelText('Comune')).toHaveValue('Torino')
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    rerenderWith({ ...AZIENDA, comune: 'Genova', updated_at: '2026-08-22T09:00:00Z' })
    expect(screen.getByLabelText('PEC')).toHaveValue('studio@pec.itx')
    expect(screen.getByLabelText('Comune')).toHaveValue('Torino')
    // What Salva writes is the reseeded row plus the draft, never the stale one.
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.comune).toBe('Torino')
    expect(saveCall()[1].body.pec).toBe('studio@pec.itx')
  })

  it('locks the fields while a save is in flight, so nothing typed then is lost', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(api.PUT).mockReturnValue(new Promise((resolve) => (finish = resolve)) as never)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByLabelText('PEC')).toBeDisabled())
    finish(ok(AZIENDA))
    await waitFor(() => expect(screen.getByLabelText('PEC')).toBeEnabled())
  })

  it('attaches the server message to the field it names', async () => {
    vi.mocked(api.PUT).mockResolvedValue(
      failed(
        {
          code: 'validation_failed',
          detail: 'partita_iva deve essere di 11 cifre',
          entity: 'emitter_profile',
          field: 'partita_iva',
        },
        422,
      ),
    )
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(await screen.findByText(/11 cifre/)).toBeInTheDocument()
    expect(screen.getByLabelText('Partita IVA')).toHaveAttribute('aria-invalid', 'true')
  })

  // ---- the version check (REB-622, spec 2026-10-03 §11) ---------------------------------

  const STALE = {
    type: 'https://pigrocrm.dev/errors/stale_row',
    title: 'Riga cambiata nel frattempo',
    status: 409,
    detail: 'qualcun altro ha salvato nel frattempo: ricarica e riprova',
    code: 'stale_row',
    entity: 'emitter_profile',
    updated_at: '2026-08-21T09:00:00Z',
  }

  it('sends the version of the row the form was seeded from, and the newer one once it followed it', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok(AZIENDA))
    const { rerenderWith } = renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.updated_at).toBe('2026-08-20T09:00:00Z')
    // Untouched, the form followed the newer row: the next save is built on it.
    rerenderWith({ ...AZIENDA, comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, options] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body.updated_at).toBe('2026-08-21T09:00:00Z')
  })

  it('shows the saved row and carries its version while the list has not refetched yet', async () => {
    // The answer is the row as saved; the prop still holds the previous row for a
    // round trip, and that older row must not be adopted back over the saved values.
    const saved = { ...AZIENDA, comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' }
    vi.mocked(api.PUT).mockResolvedValue(ok(saved))
    renderPanel()
    await userEvent.clear(screen.getByLabelText('Comune'))
    await userEvent.type(screen.getByLabelText('Comune'), 'Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByLabelText('Comune')).toBeEnabled())
    expect(screen.getByLabelText('Comune')).toHaveValue('Torino')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, options] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body).toMatchObject({ comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
  })

  it('keeps the seeded version under an open draft, so a newer row is not overwritten in silence', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok(AZIENDA))
    const { rerenderWith } = renderPanel()
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    // Another admin saved while the draft was open: the form keeps its values and its
    // version, and the server, not this form, decides what happens to the save.
    rerenderWith({ ...AZIENDA, comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    expect(screen.getByLabelText('Comune')).toHaveValue('Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.updated_at).toBe('2026-08-20T09:00:00Z')
  })

  it('Ricarica adopts a newer row that had already arrived under the draft', async () => {
    // A background refetch brought the other admin's row while the draft was open; the
    // reload fetches the same row again, and it must be adopted all the same.
    vi.mocked(api.PUT).mockResolvedValueOnce(failed(STALE, 409)).mockResolvedValue(ok(AZIENDA))
    const { rerenderWith } = renderPanel()
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    rerenderWith({ ...AZIENDA, comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    expect(screen.getByLabelText('Comune')).toHaveValue('Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('button', { name: 'Ricarica' })
    await userEvent.click(screen.getByRole('button', { name: 'Ricarica' }))
    await waitFor(() => expect(screen.getByLabelText('Comune')).toHaveValue('Torino'))
    expect(screen.getByLabelText('PEC')).toHaveValue('studio@pec.itx')
    expect(screen.getByRole('alert')).toHaveTextContent('Riga ricaricata.')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, options] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body.updated_at).toBe('2026-08-21T09:00:00Z')
  })

  it('shows a refusal of another kind beside the reload reminder, never behind it', async () => {
    vi.mocked(api.PUT)
      .mockResolvedValueOnce(failed(STALE, 409))
      .mockResolvedValue(
        failed({ code: 'permission_denied', detail: 'upsert_emitter_profile requires one of [admin], actor has collaboratore' }, 403),
      )
    const { rerenderWith } = renderPanel()
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Ricarica' }))
    rerenderWith({ ...AZIENDA, comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2))
    expect(screen.getAllByRole('alert')[0]).toHaveTextContent('Riga ricaricata.')
    expect(screen.getAllByRole('alert')[1]).toHaveTextContent(/requires one of \[admin\]/)
  })

  it('on a stale refusal offers Ricarica, which takes the newer row under the touched fields', async () => {
    vi.mocked(api.PUT).mockResolvedValueOnce(failed(STALE, 409)).mockResolvedValue(ok(AZIENDA))
    const { rerenderWith } = renderPanel()
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const banner = await screen.findByRole('alert')
    expect(banner).toHaveTextContent('Qualcun altro ha salvato nel frattempo.')
    // The draft is still in the inputs, nothing was reseeded by the refusal alone.
    expect(screen.getByLabelText('PEC')).toHaveValue('studio@pec.itx')
    await userEvent.click(screen.getByRole('button', { name: 'Ricarica' }))
    // The refetch brings the other admin's row: the untouched field follows it, the
    // touched one keeps the draft, and the banner says so until the next save lands.
    rerenderWith({ ...AZIENDA, comune: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    expect(screen.getByLabelText('Comune')).toHaveValue('Torino')
    expect(screen.getByLabelText('PEC')).toHaveValue('studio@pec.itx')
    expect(screen.getByRole('alert')).toHaveTextContent('Riga ricaricata.')
    expect(screen.queryByRole('button', { name: 'Ricarica' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, options] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body).toMatchObject({
      comune: 'Torino',
      pec: 'studio@pec.itx',
      updated_at: '2026-08-21T09:00:00Z',
    })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
