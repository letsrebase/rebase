import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AziendaPanel } from './AziendaPanel'
import type { AziendaRecord } from './queries'
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

const AZIENDA: AziendaRecord = {
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

function renderPanel(azienda: AziendaRecord = AZIENDA) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = (record: AziendaRecord) => (
    <QueryClientProvider client={client}>
      <AziendaPanel azienda={record} />
    </QueryClientProvider>
  )
  const result = render(tree(azienda))
  return { ...result, rerenderWith: (record: AziendaRecord) => result.rerender(tree(record)) }
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

describe('AziendaPanel', () => {
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
})
