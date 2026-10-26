import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { AttivitaDialog } from './AttivitaDialog'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

const ME = '33333333-3333-7333-8333-333333333333'
const COLLEAGUE = '44444444-4444-7444-8444-444444444444'
const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useIsAdmin: () => true,
  useAuth: () => ({ user: { id: ME, nome: 'Lorenzo', ruolo: 'admin' }, isLoading: false }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const users = [
  { id: ME, nome: 'Lorenzo', attivo: true },
  { id: COLLEAGUE, nome: 'Ivan', attivo: true },
  { id: '55555555-5555-7555-8555-555555555555', nome: 'Ex collega', attivo: false },
]

const search = {
  termine: 'Flore',
  gruppi: [
    { entity: 'customer', hits: [], totale: 0, totale_e_un_minimo: false },
    {
      entity: 'deal',
      hits: [
        {
          entity: 'deal',
          id: DEAL,
          etichetta: 'FloreMoria sito',
          sottotitolo: 'FloreMoria',
          punteggio: '1.0000',
          campo: 'nome',
        },
      ],
      totale: 1,
      totale_e_un_minimo: false,
    },
  ],
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/users') return ok(users)
    if (path === '/api/search') return ok(search)
    if (path === '/api/deals/{deal_id}')
      return ok({ id: DEAL, nome: 'FloreMoria sito', customer_id: null })
    throw new Error(`unexpected GET ${path}`)
  }) as never)
  vi.mocked(api.POST).mockImplementation((() => ok({ id: 'new' })) as never)
})

function renderDialog(props: Partial<React.ComponentProps<typeof AttivitaDialog>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onOpenChange = vi.fn()
  const { unmount } = render(
    <QueryClientProvider client={client}>
      <AttivitaDialog open onOpenChange={onOpenChange} {...props} />
    </QueryClientProvider>,
  )
  return { onOpenChange, unmount }
}

function posted() {
  const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
    string,
    { body: Record<string, unknown> },
  ]
  return { path, body: options.body }
}

describe('AttivitaDialog', () => {
  it('creates an attività with no deadline, linked to a deal and given to a colleague', async () => {
    const { onOpenChange } = renderDialog()

    await userEvent.type(screen.getByLabelText('Titolo'), 'Preparare le proposte')
    await userEvent.type(screen.getByLabelText('Note'), 'Con i prezzi nuovi')
    await userEvent.click(screen.getByRole('combobox', { name: 'Assegnata a' }))
    // An inactive account is never offered: the service would refuse it anyway.
    expect(screen.queryByRole('option', { name: 'Ex collega' })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByRole('option', { name: 'Ivan' }))
    await userEvent.type(screen.getByPlaceholderText(/Cerca un cliente/), 'Flore')
    await userEvent.click(await screen.findByRole('button', { name: /FloreMoria sito/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const { path, body } = posted()
    expect(path).toBe('/api/activities')
    expect(body).toEqual({
      titolo: 'Preparare le proposte',
      note: 'Con i prezzi nuovi',
      scadenza: null,
      assegnata_a: COLLEAGUE,
      customer_id: null,
      person_id: null,
      deal_id: DEAL,
      invoice_id: null,
      custom_fields: {},
    })
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })

  it('prefills what the caller knows: the day and the record it was opened from', async () => {
    renderDialog({ defaults: { scadenza: '2026-10-30', link: { kind: 'deal', id: DEAL } } })

    expect(screen.getByLabelText('Scadenza')).toHaveValue('2026-10-30')
    expect(await screen.findByText(/FloreMoria sito/)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Titolo'), 'Preparare le proposte')
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(posted().body).toMatchObject({ scadenza: '2026-10-30', deal_id: DEAL })
  })

  it('does not send a blank title, and a link can be taken off', async () => {
    renderDialog({ defaults: { link: { kind: 'deal', id: DEAL } } })

    await userEvent.type(screen.getByLabelText('Titolo'), '   ')
    expect(screen.getByRole('button', { name: 'Aggiungi' })).toBeDisabled()

    await userEvent.type(screen.getByLabelText('Titolo'), 'Qualcosa')
    await userEvent.click(screen.getByRole('button', { name: 'Scollega' }))
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(posted().body).toMatchObject({ deal_id: null, scadenza: null, assegnata_a: null })
  })
})

const existing = {
  id: 'att-1',
  titolo: 'Sollecitare Rossi',
  note: null,
  scadenza: '2026-10-30',
  stato: 'aperta' as const,
  completata_il: null,
  assegnata_a: null,
  customer_id: null,
  person_id: null,
  deal_id: DEAL,
  invoice_id: null,
  origine: 'manuale' as const,
  regola: null,
  custom_fields: {},
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

describe('AttivitaDialog on an existing attività', () => {
  beforeEach(() => {
    vi.mocked(api.PATCH).mockReset()
    vi.mocked(api.DELETE).mockReset()
    vi.mocked(api.PATCH).mockImplementation((() => ok(existing)) as never)
    vi.mocked(api.DELETE).mockImplementation((() => ok(undefined)) as never)
  })

  it('sends only what changed: the new deadline, nothing else', async () => {
    renderDialog({ attivita: existing })

    expect(screen.getByLabelText('Titolo')).toHaveValue('Sollecitare Rossi')
    fireEvent.change(screen.getByLabelText('Scadenza'), { target: { value: '2026-11-06' } })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.PATCH).mock.calls[0] as unknown as [
      string,
      { body: Record<string, unknown>; params: { path: { attivita_id: string } } },
    ]
    expect(path).toBe('/api/activities/{attivita_id}')
    expect(options.params.path.attivita_id).toBe('att-1')
    expect(options.body).toEqual({
      scadenza: '2026-11-06',
      scadenza_da_rimuovere: false,
      riferimento_da_rimuovere: false,
    })
  })

  it('takes a deadline off with null, and moves the link as four columns', async () => {
    renderDialog({ attivita: existing })

    fireEvent.change(screen.getByLabelText('Scadenza'), { target: { value: '' } })
    await userEvent.click(screen.getByRole('button', { name: 'Scollega' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    const options = vi.mocked(api.PATCH).mock.calls[0]?.[1] as unknown as {
      body: Record<string, unknown>
    }
    expect(options.body).toEqual({
      scadenza: null,
      customer_id: null,
      person_id: null,
      deal_id: null,
      invoice_id: null,
      scadenza_da_rimuovere: false,
      riferimento_da_rimuovere: false,
    })
  })

  it('reopens a completed one, and offers no «Riapri» on an open one', async () => {
    const { unmount } = renderDialog({ attivita: existing })
    expect(screen.queryByRole('button', { name: 'Riapri' })).not.toBeInTheDocument()
    unmount()

    renderDialog({ attivita: { ...existing, stato: 'completata', completata_il: '2026-10-20' } })
    await userEvent.click(screen.getByRole('button', { name: 'Riapri' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/activities/{attivita_id}/reopen')
  })

  it('archives with DELETE, and an archived one offers «Ripristina» instead', async () => {
    const { unmount } = renderDialog({ attivita: existing })
    await userEvent.click(screen.getByRole('button', { name: 'Archivia' }))
    await waitFor(() => expect(api.DELETE).toHaveBeenCalled())
    expect(vi.mocked(api.DELETE).mock.calls[0]?.[0]).toBe('/api/activities/{attivita_id}')
    unmount()

    renderDialog({ attivita: existing, archived: true })
    expect(screen.queryByRole('button', { name: 'Archivia' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/activities/{attivita_id}/restore')
  })
})
