import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ClauseNotesSection } from './ClauseNotesSection'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
const auth = vi.hoisted(() => ({ may: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.may }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const RENEWAL = {
  id: 'n1',
  contract_id: 'c1',
  riferimento_clausola: 'Art. 8.3 Rinnovo',
  testo_letterale: 'Il contratto si rinnova\nsalvo disdetta.',
  interpretazione_adottata: 'Il rinnovo è tacito per dodici mesi.',
  note: 'Confermato al telefono.',
  created_at: '2026-10-01T09:00:00Z',
  updated_at: '2026-10-01T09:00:00Z',
}
const PAYMENT = {
  ...RENEWAL,
  id: 'n2',
  riferimento_clausola: 'Art. 5 Pagamenti',
  testo_letterale: 'Pagamento a trenta giorni.',
  interpretazione_adottata: 'Trenta giorni data fattura.',
  note: null,
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <ClauseNotesSection contractId="c1" />
    </QueryClientProvider>,
  )
  return { invalidate }
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.GET).mockImplementation((() => ok([RENEWAL, PAYMENT])) as never)
  auth.may = true
})

describe('ClauseNotesSection', () => {
  it('lists each note with the clause, the verbatim text, the reading and the optional note', async () => {
    renderSection()
    expect(await screen.findByText('Art. 8.3 Rinnovo')).toBeInTheDocument()
    const verbatim = screen.getByText(/Il contratto si rinnova/)
    expect(verbatim).toHaveTextContent('Il contratto si rinnova salvo disdetta.')
    expect(verbatim.className).toContain('whitespace-pre-wrap')
    expect(screen.getByText('Il rinnovo è tacito per dodici mesi.')).toBeInTheDocument()
    expect(screen.getByText('Confermato al telefono.')).toBeInTheDocument()
    expect(screen.getByText('Art. 5 Pagamenti')).toBeInTheDocument()
    expect(screen.getAllByText('Testo')).toHaveLength(2)
    expect(screen.getAllByText('Interpretazione adottata')).toHaveLength(2)
    // Only the first note has an optional note to label.
    expect(screen.getAllByText('Note')).toHaveLength(1)
  })

  it('says what a note is for when there is none', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok([])) as never)
    renderSection()
    expect(
      await screen.findByText(
        'Nessuna nota: scrivi come hai letto una clausola ambigua, accanto alle sue parole.',
      ),
    ).toBeInTheDocument()
  })

  it('posts the new note and refreshes the list, the contract and its timeline', async () => {
    vi.mocked(api.POST).mockImplementation((() => ok({ ...PAYMENT, id: 'n3' })) as never)
    const { invalidate } = renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Aggiungi una nota' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(screen.getByLabelText('Clausola'), 'Art. 9 Recesso')
    await userEvent.type(screen.getByLabelText('Testo letterale'), 'Recesso con preavviso.')
    await userEvent.type(screen.getByLabelText('Interpretazione adottata'), 'Sessanta giorni.')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/clause-notes',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' } },
        body: {
          riferimento_clausola: 'Art. 9 Recesso',
          testo_letterale: 'Recesso con preavviso.',
          interpretazione_adottata: 'Sessanta giorni.',
          note: null,
        },
      }),
    )
    await vi.waitFor(() => expect(dialog).not.toBeInTheDocument())
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey)
    expect(keys).toContainEqual(['contract', 'c1', 'clause-notes'])
    expect(keys).toContainEqual(['contract', 'c1'])
    expect(keys).toContainEqual(['timeline', 'contract', 'c1'])
  })

  it('shows the API’s refusal under the field it names and keeps the dialog open', async () => {
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        {
          type: 'about:blank',
          title: 'Unprocessable',
          status: 422,
          code: 'validation_failed',
          field: 'testo_letterale',
          detail: 'testo_letterale: il valore non può essere vuoto',
        },
        422,
      )) as never)
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Aggiungi una nota' }))
    await userEvent.type(screen.getByLabelText('Clausola'), 'Art. 9 Recesso')
    await userEvent.type(screen.getByLabelText('Interpretazione adottata'), 'Sessanta giorni.')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(await screen.findByText(/il valore non può essere vuoto/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('patches only the fields that changed, and clears the optional note with null', async () => {
    vi.mocked(api.PATCH).mockImplementation((() => ok(RENEWAL)) as never)
    renderSection()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Modifica la nota Art. 8.3 Rinnovo' }),
    )
    const reading = screen.getByLabelText('Interpretazione adottata')
    await userEvent.clear(reading)
    await userEvent.type(reading, 'Il rinnovo è tacito per sei mesi.')
    await userEvent.clear(screen.getByLabelText('Note (facoltative)'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(api.PATCH).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/clause-notes/{note_id}',
      expect.objectContaining({
        params: { path: { contract_id: 'c1', note_id: 'n1' } },
        body: {
          interpretazione_adottata: 'Il rinnovo è tacito per sei mesi.',
          note: null,
        },
      }),
    )
  })

  it('writes nothing when an edit changes nothing', async () => {
    renderSection()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Modifica la nota Art. 5 Pagamenti' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(api.PATCH).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('shows the list and no buttons to a person who may not write', async () => {
    auth.may = false
    renderSection()
    expect(await screen.findByText('Art. 8.3 Rinnovo')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Aggiungi una nota' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Modifica/ })).not.toBeInTheDocument()
  })
})
