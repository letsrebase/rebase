import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const LEGAL_ENTITY: LegalEntityRecord = {
  id: 'a-1',
  name: 'Studio Rossi',
  is_default: true,
  active: true,
  legal_name: 'Studio Rossi',
  vat_number: '12345678901',
  tax_code: null,
  address: 'Via Roma 1',
  postal_code: '20100',
  city: 'Milano',
  province: 'MI',
  country: 'IT',
  certified_email: 'studio@pec.it',
  sdi_code: null,
  phone: null,
  email: null,
  website: null,
  logo_key: null,
  signature_key: null,
  email_signature: null,
  tax_regime: 'RF19',
  legal_representative: null,
  fiscal_year_end_month: 12,
  fiscal_year_end_day: 31,
  registration_date: null,
  place: {
    status: 'matched',
    municipality_code: '015146',
    cadastral_code: 'F205',
    municipality_name: 'Milano',
    region_code: '03',
    region_name: 'Lombardia',
  },
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

function renderPanel(legal_entity: LegalEntityRecord = LEGAL_ENTITY) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = (record: LegalEntityRecord) => (
    <QueryClientProvider client={client}>
      <LegalEntityPanel legal_entity={record} />
    </QueryClientProvider>
  )
  const result = render(tree(legal_entity))
  return { ...result, rerenderWith: (record: LegalEntityRecord) => result.rerender(tree(record)) }
}

function saveCall() {
  return vi.mocked(api.PUT).mock.calls[0] as unknown as [
    string,
    { params: { path: { legal_entity_id: string } }; body: Record<string, unknown> },
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
    renderPanel({ ...LEGAL_ENTITY, vat_number: null, address: null, certified_email: null, tax_regime: null })
    expect(screen.getByLabelText(/Ragione sociale/)).toHaveValue('Studio Rossi')
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('')
    expect(screen.getByLabelText('Nazione')).toHaveValue('IT')
  })

  it('saves to the azienda by id and sends an emptied field as an empty string', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok({ ...LEGAL_ENTITY, certified_email: '' }))
    renderPanel()
    await userEvent.clear(screen.getByLabelText('PEC'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    const [path, options] = saveCall()
    expect(path).toBe('/api/legal-entities/{legal_entity_id}')
    expect(options.params.path.legal_entity_id).toBe('a-1')
    expect(options.body.certified_email).toBe('')
    expect(options.body.name).toBe('Studio Rossi')
  })

  it('types the day of the application for registration and sends it, or null when empty (REB-1100)', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok(LEGAL_ENTITY))
    renderPanel()
    const field = screen.getByLabelText('Domanda di iscrizione al registro imprese')
    expect(field).toHaveAttribute('type', 'date')
    expect(field).toHaveValue('')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.registration_date).toBeNull()

    fireEvent.change(field, { target: { value: '2026-03-04' } })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const second = vi.mocked(api.PUT).mock.calls[1] as unknown as [
      string,
      { body: Record<string, unknown> },
    ]
    expect(second[1].body.registration_date).toBe('2026-03-04')
  })

  it('follows a newer row while untouched and keeps a draft once typing started', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok(LEGAL_ENTITY))
    const { rerenderWith } = renderPanel()
    // Another admin saved: the refetched row is newer and the form has no draft.
    rerenderWith({ ...LEGAL_ENTITY, city: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    expect(screen.getByLabelText('Comune')).toHaveValue('Torino')
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    rerenderWith({ ...LEGAL_ENTITY, city: 'Genova', updated_at: '2026-08-22T09:00:00Z' })
    expect(screen.getByLabelText('PEC')).toHaveValue('studio@pec.itx')
    expect(screen.getByLabelText('Comune')).toHaveValue('Torino')
    // What Salva writes is the reseeded row plus the draft, never the stale one.
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.city).toBe('Torino')
    expect(saveCall()[1].body.certified_email).toBe('studio@pec.itx')
  })

  it('locks the fields while a save is in flight, so nothing typed then is lost', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(api.PUT).mockReturnValue(new Promise((resolve) => (finish = resolve)) as never)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByLabelText('PEC')).toBeDisabled())
    finish(ok(LEGAL_ENTITY))
    await waitFor(() => expect(screen.getByLabelText('PEC')).toBeEnabled())
  })

  it('attaches the server message to the field it names', async () => {
    vi.mocked(api.PUT).mockResolvedValue(
      failed(
        {
          code: 'validation_failed',
          detail: 'partita_iva deve essere di 11 cifre',
          entity: 'legal_entities',
          field: 'vat_number',
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
    entity: 'legal_entities',
    updated_at: '2026-08-21T09:00:00Z',
  }

  it('sends the version of the row the form was seeded from, and the newer one once it followed it', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok(LEGAL_ENTITY))
    const { rerenderWith } = renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.updated_at).toBe('2026-08-20T09:00:00Z')
    // Untouched, the form followed the newer row: the next save is built on it.
    rerenderWith({ ...LEGAL_ENTITY, city: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, options] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body.updated_at).toBe('2026-08-21T09:00:00Z')
  })

  it('shows the saved row and carries its version while the list has not refetched yet', async () => {
    // The answer is the row as saved; the prop still holds the previous row for a
    // round trip, and that older row must not be adopted back over the saved values.
    const saved = { ...LEGAL_ENTITY, city: 'Torino', updated_at: '2026-08-21T09:00:00Z' }
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
    expect(options.body).toMatchObject({ city: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
  })

  it('keeps the seeded version under an open draft, so a newer row is not overwritten in silence', async () => {
    vi.mocked(api.PUT).mockResolvedValue(ok(LEGAL_ENTITY))
    const { rerenderWith } = renderPanel()
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    // Another admin saved while the draft was open: the form keeps its values and its
    // version, and the server, not this form, decides what happens to the save.
    rerenderWith({ ...LEGAL_ENTITY, city: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    expect(screen.getByLabelText('Comune')).toHaveValue('Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.updated_at).toBe('2026-08-20T09:00:00Z')
  })

  it('Ricarica adopts a newer row that had already arrived under the draft', async () => {
    // A background refetch brought the other admin's row while the draft was open; the
    // reload fetches the same row again, and it must be adopted all the same.
    vi.mocked(api.PUT).mockResolvedValueOnce(failed(STALE, 409)).mockResolvedValue(ok(LEGAL_ENTITY))
    const { rerenderWith } = renderPanel()
    await userEvent.type(screen.getByLabelText('PEC'), 'x')
    rerenderWith({ ...LEGAL_ENTITY, city: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
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
    rerenderWith({ ...LEGAL_ENTITY, city: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2))
    expect(screen.getAllByRole('alert')[0]).toHaveTextContent('Riga ricaricata.')
    expect(screen.getAllByRole('alert')[1]).toHaveTextContent(/requires one of \[admin\]/)
  })

  it('on a stale refusal offers Ricarica, which takes the newer row under the touched fields', async () => {
    vi.mocked(api.PUT).mockResolvedValueOnce(failed(STALE, 409)).mockResolvedValue(ok(LEGAL_ENTITY))
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
    rerenderWith({ ...LEGAL_ENTITY, city: 'Torino', updated_at: '2026-08-21T09:00:00Z' })
    expect(screen.getByLabelText('Comune')).toHaveValue('Torino')
    expect(screen.getByLabelText('PEC')).toHaveValue('studio@pec.itx')
    expect(screen.getByRole('alert')).toHaveTextContent('Riga ricaricata.')
    expect(screen.queryByRole('button', { name: 'Ricarica' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, options] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body).toMatchObject({
      city: 'Torino',
      certified_email: 'studio@pec.itx',
      updated_at: '2026-08-21T09:00:00Z',
    })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})

describe('LegalEntityPanel: the place the address names (REB-1069)', () => {
  const UNKNOWN: LegalEntityRecord = {
    ...LEGAL_ENTITY,
    city: 'Reggio Emilia',
    province: 'RE',
    place: { status: 'unknown', region_code: '08', region_name: 'Emilia-Romagna' },
  }
  const OPTIONS = [
    { municipality_code: '035032', cadastral_code: 'H122', name: 'Reggiolo' },
    { municipality_code: '035033', cadastral_code: 'H223', name: "Reggio nell'Emilia" },
  ]

  it('says which municipality and region the saved address was read as', () => {
    renderPanel()
    expect(screen.getByTestId('place-matched')).toHaveTextContent(
      'Comune riconosciuto: Milano, Lombardia (codice ISTAT 015146).',
    )
    expect(screen.queryByTestId('place-unknown')).not.toBeInTheDocument()
  })

  it('says nothing about a municipality for an address abroad', () => {
    renderPanel({ ...LEGAL_ENTITY, country: 'FR', place: { status: 'not_italian' } })
    expect(screen.queryByTestId('place-matched')).not.toBeInTheDocument()
    expect(screen.queryByTestId('place-unknown')).not.toBeInTheDocument()
  })

  it('lets the person pick the municipality of the province when the comune matches none, and sends its name', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(OPTIONS))
    vi.mocked(api.PUT).mockResolvedValue(ok(LEGAL_ENTITY))
    renderPanel(UNKNOWN)
    expect(screen.getByTestId('place-unknown')).toHaveTextContent('Comune non riconosciuto')
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/legal-entities/municipalities', {
        params: { query: { province: 'RE' } },
      }),
    )
    await userEvent.click(await screen.findByRole('combobox', { name: /Comune dell’elenco ISTAT/ }))
    await userEvent.click(await screen.findByRole('option', { name: "Reggio nell'Emilia" }))
    expect(screen.getByLabelText('Comune')).toHaveValue("Reggio nell'Emilia")
    // The answer described the address as saved: it goes while the comune is being changed.
    expect(screen.queryByTestId('place-unknown')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    expect(saveCall()[1].body.city).toBe("Reggio nell'Emilia")
  })

  it('does not ask for a list until the province is a two-letter code', () => {
    renderPanel({ ...UNKNOWN, province: null })
    expect(screen.getByTestId('place-unknown')).toHaveTextContent('Indica la provincia')
    // The sedi section reads the fiscal profile; the list of municipalities is what is not asked.
    expect(api.GET).not.toHaveBeenCalledWith('/api/legal-entities/municipalities', expect.anything())
  })

  it('says so when the province is a code the list does not carry', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok([]))
    renderPanel({ ...UNKNOWN, province: 'XX' })
    expect(await screen.findByText(/La sigla XX non è nell’elenco ISTAT/)).toBeInTheDocument()
  })

  it('says so when the list cannot be loaded, instead of offering a dead control', async () => {
    vi.mocked(api.GET).mockResolvedValue(failed({ code: 'internal_error', detail: 'boom' }, 500))
    renderPanel(UNKNOWN)
    expect(await screen.findByText(/Elenco dei comuni non disponibile/)).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /Comune dell’elenco ISTAT/ })).not.toBeInTheDocument()
  })

  it('asks nothing of an address with no comune yet', () => {
    renderPanel({ ...UNKNOWN, city: null })
    expect(screen.queryByTestId('place-unknown')).not.toBeInTheDocument()
  })
})
