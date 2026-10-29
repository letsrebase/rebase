import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Note } from '@/lib/api'
import { Notes } from './Notes'

const THREAD: Note[] = [
  {
    id: 'n3',
    testo: 'CV caricato dalla persona',
    autore: 'Ada Lovelace',
    autore_id: 'u9',
    fonte: 'persona',
    created_at: '2026-09-10T08:00:00Z',
    modificata_at: null,
    modificata_da: null,
    modificabile: false,
  },
  {
    id: 'n2',
    testo: 'Ha mandato il portfolio.',
    autore: 'Ivan',
    autore_id: 'u1',
    fonte: 'mcp',
    created_at: '2026-09-09T15:30:00Z',
    modificata_at: '2026-09-10T07:00:00Z',
    modificata_da: 'Lorenzo',
    modificabile: true,
  },
  {
    id: 'n1',
    testo: 'Sentito al telefono.\nRichiamare lunedì.',
    autore: 'Ivan',
    autore_id: 'u1',
    fonte: null,
    created_at: '2026-09-08T09:05:00Z',
    modificata_at: null,
    modificata_da: null,
    modificabile: true,
  },
]

/** The detail page's part of the contract: it holds the thread and takes back what the
 *  component reports after a change, the way the page's query cache does. */
function Harness({ initial }: { initial: Note[] }) {
  const [notes, setNotes] = useState(initial)
  return <Notes kind="freelancers" id="f1" notes={notes} onChange={setNotes} />
}

function mount(initial: Note[]) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Harness initial={initial} />
    </QueryClientProvider>,
  )
}

function answer(status: number, body?: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status })
}

afterEach(() => vi.restoreAllMocks())

describe('Notes', () => {
  it('renders the thread as given with author, date, source and who last edited it', () => {
    mount(THREAD)
    const section = screen.getByRole('region', { name: 'Note' })
    const items = within(section).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Ada Lovelace')
    expect(items[0]).toHaveTextContent('dalla persona')
    expect(items[1]).toHaveTextContent('Ivan')
    expect(items[1]).toHaveTextContent('MCP')
    expect(items[1]).toHaveTextContent(/9 set 2026/)
    expect(items[1]).toHaveTextContent(/modificata da Lorenzo il 10 set 2026/)
    // A note from before the source was tracked has no badge and no edit line.
    expect(items[2]).not.toHaveTextContent('MCP')
    expect(items[2]).not.toHaveTextContent('modificata')
    expect(within(items[2]!).getByText(/Richiamare lunedì/)).toHaveClass('whitespace-pre-wrap')
  })

  it('offers edit and delete only on the notes an admin may change', () => {
    mount(THREAD)
    const [event, mine, old] = screen.getAllByRole('listitem')
    expect(within(event!).queryByRole('button')).toBeNull()
    expect(within(mine!).getByRole('button', { name: 'Modifica' })).toBeInTheDocument()
    expect(within(old!).getByRole('button', { name: 'Elimina' })).toBeInTheDocument()
  })

  it('says so when there is nothing yet', () => {
    mount([])
    expect(screen.getByText('Nessuna nota, per ora.')).toBeInTheDocument()
  })

  it('posts the text to the entity route, clears the box and shows the new note on top', async () => {
    const user = userEvent.setup()
    const created: Note = {
      id: 'n4',
      testo: 'Richiamato: parte a ottobre.',
      autore: 'Ivan',
      autore_id: 'u1',
      fonte: 'web',
      created_at: '2026-09-10T10:00:00Z',
      modificata_at: null,
      modificata_da: null,
      modificabile: true,
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(201, created))
    mount(THREAD)

    const box = screen.getByLabelText('Nuova nota')
    const button = screen.getByRole('button', { name: 'Aggiungi nota' })
    expect(button).toBeDisabled()
    await user.type(box, '  Richiamato: parte a ottobre.  ')
    await user.click(button)

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(4))
    const first = screen.getAllByRole('listitem')[0]!
    expect(first).toHaveTextContent('Richiamato: parte a ottobre.')
    expect(first).toHaveTextContent('dal sito')
    expect(box).toHaveValue('')

    const [url, init] = fetchSpy.mock.calls[0]!
    expect(url).toBe('/api/hub/freelancers/f1/notes')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ testo: 'Richiamato: parte a ottobre.' })
  })

  it('edits a note in place through PATCH and shows who edited it', async () => {
    const user = userEvent.setup()
    const edited: Note = {
      ...THREAD[2]!,
      testo: 'Sentito lunedì.',
      modificata_at: '2026-09-11T10:00:00Z',
      modificata_da: 'Ivan',
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, edited))
    mount(THREAD)

    const item = screen.getAllByRole('listitem')[2]!
    await user.click(within(item).getByRole('button', { name: 'Modifica' }))
    const box = within(item).getByLabelText('Testo della nota')
    expect(box).toHaveValue('Sentito al telefono.\nRichiamare lunedì.')
    await user.clear(box)
    await user.type(box, 'Sentito lunedì.')
    await user.click(within(item).getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(screen.getAllByRole('listitem')[2]).toHaveTextContent('Sentito lunedì.'))
    expect(screen.getAllByRole('listitem')[2]).toHaveTextContent(/modificata da Ivan il 11 set 2026/)
    expect(screen.queryByLabelText('Testo della nota')).toBeNull()
    const [url, init] = fetchSpy.mock.calls[0]!
    expect(url).toBe('/api/hub/notes/n1')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(String(init?.body))).toEqual({ testo: 'Sentito lunedì.' })
  })

  it('leaves the text alone when the edit is cancelled', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    mount(THREAD)
    const item = screen.getAllByRole('listitem')[2]!
    await user.click(within(item).getByRole('button', { name: 'Modifica' }))
    await user.type(within(item).getByLabelText('Testo della nota'), ' in più')
    await user.click(within(item).getByRole('button', { name: 'Annulla' }))
    expect(screen.getAllByRole('listitem')[2]).toHaveTextContent('Sentito al telefono.')
    expect(screen.getAllByRole('listitem')[2]).not.toHaveTextContent('in più')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('deletes only after a second click and drops the note from the thread', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(204))
    mount(THREAD)

    const item = screen.getAllByRole('listitem')[1]!
    await user.click(within(item).getByRole('button', { name: 'Elimina' }))
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(within(item).getByText('Eliminare questa nota?')).toBeInTheDocument()
    await user.click(within(item).getByRole('button', { name: 'Conferma' }))

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2))
    expect(screen.queryByText('Ha mandato il portfolio.')).toBeNull()
    const [url, init] = fetchSpy.mock.calls[0]!
    expect(url).toBe('/api/hub/notes/n2')
    expect(init?.method).toBe('DELETE')
  })

  it('keeps a deleted note gone when an add resolves while its delete is still pending', async () => {
    const user = userEvent.setup()
    const created: Note = { ...THREAD[2]!, id: 'n4', testo: 'Nuova.', fonte: 'web' }
    let finishDelete: () => void = () => {}
    const deletion = new Promise<Response>((resolve) => {
      finishDelete = () => resolve(answer(204))
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) =>
      init?.method === 'DELETE' ? deletion : answer(201, created),
    )
    mount(THREAD)

    const item = screen.getAllByRole('listitem')[1]!
    await user.click(within(item).getByRole('button', { name: 'Elimina' }))
    await user.click(within(item).getByRole('button', { name: 'Conferma' }))
    // The delete is in flight: an add goes through and resolves first.
    await user.type(screen.getByLabelText('Nuova nota'), 'Nuova.')
    await user.click(screen.getByRole('button', { name: 'Aggiungi nota' }))
    await waitFor(() => expect(screen.getByText('Nuova.')).toBeInTheDocument())
    expect(screen.getByText('Ha mandato il portfolio.')).toBeInTheDocument()

    finishDelete()
    await waitFor(() => expect(screen.queryByText('Ha mandato il portfolio.')).toBeNull())
    // Both survive: the new note was not lost to a list taken before it, and the deleted one did not return.
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      expect.stringContaining('Nuova.'),
      expect.stringContaining('CV caricato dalla persona'),
      expect.stringContaining('Sentito al telefono.'),
    ])
  })

  it('keeps the draft and shows the sentence when the API refuses', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(422, {
        detail: [{ loc: ['body', 'testo'], msg: 'una nota può avere al massimo 4000 caratteri' }],
      }),
    )
    mount([])
    await user.type(screen.getByLabelText('Nuova nota'), 'troppo lunga')
    await user.click(screen.getByRole('button', { name: 'Aggiungi nota' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/al massimo 4000/)
    expect(screen.getByLabelText('Nuova nota')).toHaveValue('troppo lunga')
  })
})
