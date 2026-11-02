import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { NotesSection } from './NotesSection'
import type { Note } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
  }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
const auth = vi.hoisted(() => ({ may: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.may }))

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const OWN: Note = {
  id: 'n1',
  entity_type: 'deal',
  entity_id: 'd1',
  testo: 'Richiamare a settembre.\nPortare il preventivo.',
  autore: 'Giulia Rossi',
  autore_id: 'u1',
  fonte: 'web',
  created_at: '2026-10-01T09:00:00Z',
  modificata_at: null,
  modificata_da: null,
  modificabile: true,
}
const EDITED: Note = {
  ...OWN,
  id: 'n2',
  testo: 'Cliente interessato al piano annuale.',
  autore: 'Marco Bianchi',
  autore_id: 'u2',
  fonte: 'mcp',
  modificata_at: '2026-10-02T10:30:00Z',
  modificata_da: 'Giulia Rossi',
  modificabile: false,
}
const LEGACY: Note = {
  ...OWN,
  id: 'n3',
  testo: 'Vecchia nota sul cliente.',
  autore: null,
  autore_id: null,
  fonte: null,
  modificabile: false,
}
const SYSTEM: Note = {
  ...LEGACY,
  id: 'n4',
  testo: 'Richiesta ricevuta dall’hub.',
  fonte: 'sistema',
}
const REBASE: Note = { ...LEGACY, id: 'n5', testo: 'Firmato il contratto quadro.', fonte: 'rebase' }

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <NotesSection entityType="deal" entityId="d1" />
    </QueryClientProvider>,
  )
  return { invalidate }
}

/** The list item whose text holds `text`. */
async function item(text: string | RegExp) {
  return (await screen.findByText(text)).closest('li') as HTMLElement
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
  vi.mocked(api.GET).mockImplementation((() => ok([OWN, EDITED, LEGACY, SYSTEM, REBASE])) as never)
  auth.may = true
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('NotesSection', () => {
  it('reads the thread of the entity from the collection its type maps to', async () => {
    renderSection()
    await screen.findByText('Richiamare a settembre.', { exact: false })
    expect(api.GET).toHaveBeenCalledWith('/api/{collection}/{entity_id}/notes', {
      params: { path: { collection: 'deals', entity_id: 'd1' } },
    })
  })

  it('signs each note with its author and date, keeping the text as written', async () => {
    renderSection()
    const own = await item(/Richiamare a settembre/)
    expect(within(own).getByText('Giulia Rossi')).toBeInTheDocument()
    expect(own.querySelector('time')).toHaveAttribute('datetime', '2026-10-01T09:00:00Z')
    const text = within(own).getByText(/Richiamare a settembre/)
    expect(text).toHaveTextContent('Richiamare a settembre. Portare il preventivo.')
    expect(text.className).toContain('whitespace-pre-wrap')
    // A note written in the browser is the ordinary one: no door is named.
    expect(within(own).queryByText('via MCP')).not.toBeInTheDocument()
    expect(within(own).queryByText(/^modificata/)).not.toBeInTheDocument()
  })

  it('marks a note an agent wrote and one that was edited, with who and when as text', async () => {
    renderSection()
    const edited = await item(/piano annuale/)
    expect(within(edited).getByText('Marco Bianchi')).toBeInTheDocument()
    expect(within(edited).getByText('via MCP')).toBeInTheDocument()
    // Visible text, not a tooltip: who changed it and when.
    expect(within(edited).getByText(/^modificata da Giulia Rossi il /)).toBeVisible()
  })

  it('signs a note with no author by its source: previous, system, rebase', async () => {
    renderSection()
    const legacy = await item(/Vecchia nota/)
    expect(within(legacy).getByText('Nota precedente')).toBeInTheDocument()
    const system = await item(/Richiesta ricevuta/)
    // The source is the signature, so the badge does not say it a second time.
    expect(within(system).getAllByText('Sistema')).toHaveLength(1)
    const rebase = await item(/Firmato il contratto/)
    expect(within(rebase).getAllByText('rebase')).toHaveLength(1)
  })

  it('names the door of a system note that does carry an author', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok([{ ...SYSTEM, autore: 'Giulia Rossi' }])) as never)
    renderSection()
    const system = await item(/Richiesta ricevuta/)
    expect(within(system).getByText('Giulia Rossi')).toBeInTheDocument()
    expect(within(system).getByText('Sistema')).toBeInTheDocument()
  })

  it('says there is nothing yet when the thread is empty', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok([])) as never)
    renderSection()
    expect(await screen.findByText('Nessuna nota, per ora.')).toBeInTheDocument()
  })

  it('shows the API’s own message when the thread cannot be read', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
      failed({ code: 'http_error', detail: 'Il server non risponde.', status: 503 }, 503)) as never)
    renderSection()
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
  })

  it('holds a loading state until the thread arrives', async () => {
    renderSection()
    expect(screen.getByRole('status', { name: 'Caricamento note' })).toBeInTheDocument()
    await screen.findByText(/Richiamare a settembre/)
    expect(screen.queryByRole('status', { name: 'Caricamento note' })).not.toBeInTheDocument()
  })

  it('posts only the text, clears the box and refreshes the thread and the timeline', async () => {
    vi.mocked(api.POST).mockImplementation((() => ok({ ...OWN, id: 'n9' }, 201)) as never)
    const { invalidate } = renderSection()
    await screen.findByText(/Richiamare a settembre/)
    const box = screen.getByLabelText('Nuova nota')
    const add = screen.getByRole('button', { name: 'Aggiungi nota' })
    // Nothing to add yet: a note of nothing is not a note.
    expect(add).toBeDisabled()
    await userEvent.type(box, '  Telefonata fatta  ')
    await userEvent.click(add)

    expect(api.POST).toHaveBeenCalledWith('/api/{collection}/{entity_id}/notes', {
      params: { path: { collection: 'deals', entity_id: 'd1' } },
      body: { testo: 'Telefonata fatta' },
    })
    await vi.waitFor(() => expect(box).toHaveValue(''))
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey)
    expect(keys).toContainEqual(['notes', 'deal', 'd1'])
    expect(keys).toContainEqual(['timeline', 'deal', 'd1'])
  })

  it('keeps the text and shows the refusal when the note is not saved', async () => {
    vi.mocked(api.POST).mockImplementation((() =>
      failed(
        { type: 'about:blank', title: 'Forbidden', status: 403, code: 'permission_denied', detail: 'Non puoi scrivere qui.' },
        403,
      )) as never)
    renderSection()
    await screen.findByText(/Richiamare a settembre/)
    await userEvent.type(screen.getByLabelText('Nuova nota'), 'Una nota')
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi nota' }))

    expect(await screen.findByText('Non puoi scrivere qui.')).toBeInTheDocument()
    expect(screen.getByLabelText('Nuova nota')).toHaveValue('Una nota')
  })

  it('edits a note in place, patches its new text and refreshes the thread and the timeline', async () => {
    vi.mocked(api.PATCH).mockImplementation((() => ok({ ...OWN, testo: 'Richiamare a ottobre.' })) as never)
    const { invalidate } = renderSection()
    const own = await item(/Richiamare a settembre/)
    await userEvent.click(within(own).getByRole('button', { name: /^Modifica la nota di/ }))
    const box = within(own).getByLabelText('Testo della nota')
    expect(box).toHaveValue('Richiamare a settembre.\nPortare il preventivo.')
    await userEvent.clear(box)
    await userEvent.type(box, 'Richiamare a ottobre.')
    await userEvent.click(within(own).getByRole('button', { name: 'Salva' }))

    expect(api.PATCH).toHaveBeenCalledWith('/api/notes/{note_id}', {
      params: { path: { note_id: 'n1' } },
      body: { testo: 'Richiamare a ottobre.' },
    })
    await vi.waitFor(() =>
      expect(within(own).queryByLabelText('Testo della nota')).not.toBeInTheDocument(),
    )
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey)
    expect(keys).toContainEqual(['notes', 'deal', 'd1'])
    expect(keys).toContainEqual(['timeline', 'deal', 'd1'])
  })

  it('writes nothing when an edit leaves the text as it was, and Annulla drops it', async () => {
    renderSection()
    const own = await item(/Richiamare a settembre/)
    await userEvent.click(within(own).getByRole('button', { name: /^Modifica la nota di/ }))
    await userEvent.click(within(own).getByRole('button', { name: 'Salva' }))
    expect(api.PATCH).not.toHaveBeenCalled()
    expect(within(own).queryByLabelText('Testo della nota')).not.toBeInTheDocument()

    await userEvent.click(within(own).getByRole('button', { name: /^Modifica la nota di/ }))
    await userEvent.type(within(own).getByLabelText('Testo della nota'), ' extra')
    await userEvent.click(within(own).getByRole('button', { name: 'Annulla' }))
    expect(api.PATCH).not.toHaveBeenCalled()
    expect(within(own).getByText(/Richiamare a settembre/)).toBeInTheDocument()
  })

  it('does not save an edit emptied of its text', async () => {
    renderSection()
    const own = await item(/Richiamare a settembre/)
    await userEvent.click(within(own).getByRole('button', { name: /^Modifica la nota di/ }))
    await userEvent.clear(within(own).getByLabelText('Testo della nota'))
    expect(within(own).getByRole('button', { name: 'Salva' })).toBeDisabled()
  })

  it('deletes a note once the confirmation is accepted, and refreshes the thread and the timeline', async () => {
    vi.mocked(api.DELETE).mockImplementation((() => ok(undefined, 204)) as never)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { invalidate } = renderSection()
    const own = await item(/Richiamare a settembre/)
    await userEvent.click(within(own).getByRole('button', { name: /^Elimina la nota di/ }))

    expect(confirm).toHaveBeenCalledOnce()
    expect(api.DELETE).toHaveBeenCalledWith('/api/notes/{note_id}', {
      params: { path: { note_id: 'n1' } },
    })
    await vi.waitFor(() => {
      const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey)
      expect(keys).toContainEqual(['notes', 'deal', 'd1'])
      expect(keys).toContainEqual(['timeline', 'deal', 'd1'])
    })
  })

  it('keeps the note when the confirmation is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderSection()
    const own = await item(/Richiamare a settembre/)
    await userEvent.click(within(own).getByRole('button', { name: /^Elimina la nota di/ }))
    expect(api.DELETE).not.toHaveBeenCalled()
  })

  it('offers edit and delete only on the notes the API says may be changed', async () => {
    renderSection()
    await screen.findByText(/Richiamare a settembre/)
    // Five notes, one `modificabile`: one pair of buttons, not five.
    expect(screen.getAllByRole('button', { name: /^Modifica la nota di/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /^Elimina la nota di/ })).toHaveLength(1)
  })

  it('shows the thread and no form or buttons to a person who may not write', async () => {
    auth.may = false
    renderSection()
    expect(await screen.findByText(/Richiamare a settembre/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Nuova nota')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Aggiungi nota' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Modifica la nota di/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Elimina la nota di/ })).not.toBeInTheDocument()
  })
})
