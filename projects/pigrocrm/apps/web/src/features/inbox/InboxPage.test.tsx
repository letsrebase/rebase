import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, fetchWithRefresh } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { InboxPage } from './InboxPage'
import { detail, item, ok, refused, renderWithQuery, viewport } from './testing'
import type { InboxItem, InboxTab } from './labels'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() }, fetchWithRefresh: vi.fn() }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}>{children}</a>
  ),
  useNavigate: () => vi.fn(),
}))

const auth = { role: 'admin' as 'readonly' | 'collaborator' | 'admin' }
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@example.it', name: 'Anna', role: auth.role } }),
  useCan: () => auth.role === 'admin',
  useCanWrite: () => auth.role !== 'readonly',
  useIsAdmin: () => auth.role === 'admin',
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const NEW_RECEIPT = item({ id: '0199aaaa-0000-7000-8000-000000000003', original_name: 'scontrino.pdf' })
const PROPOSED = item({
  id: '0199aaaa-0000-7000-8000-000000000004',
  original_name: 'fattura-luce.pdf',
  state: 'proposed',
  kind: 'supplier_invoice',
  proposal_id: 'p1',
  reason: 'Proposta in attesa di una persona',
  legal_entity_id: 'le1',
})
const OLDER = item({
  id: '0199aaaa-0000-7000-8000-000000000002',
  original_name: 'estratto.csv',
  file_type: 'csv',
  kind: 'bank_statement',
  uploaded_by_id: 'u2',
  reason: 'Estratto conto: lo legge il modulo cassa, che non c’è ancora',
})

interface Serving {
  byState?: Partial<Record<string, InboxItem[]>>
  model?: boolean
  pendingProposals?: unknown[]
  unread?: InboxItem[]
}

function serving({ byState = {}, model = false, pendingProposals = [], unread = [] }: Serving = {}) {
  vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: Record<string, unknown>; path?: Record<string, string> } }) => {
    const query = init?.params?.query ?? {}
    if (path === '/api/inbox/items') {
      if (query.classified === false) return Promise.resolve(ok({ items: unread, next_cursor: null }))
      const all = byState[String(query.state)] ?? []
      const cursor = query.cursor
      if (cursor === undefined && all.length > 50) {
        return Promise.resolve(ok({ items: all.slice(0, 50), next_cursor: all[49]?.id }))
      }
      return Promise.resolve(ok({ items: cursor === undefined ? all : all.slice(50), next_cursor: null }))
    }
    if (path === '/api/inbox/items/{item_id}') {
      const id = init?.params?.path?.item_id
      if (id === 'zip1') return Promise.resolve(ok(detail({ id: 'zip1', original_name: 'lotto.zip', file_type: 'zip' })))
      return Promise.resolve(ok(detail({ id })))
    }
    if (path === '/api/proposals') return Promise.resolve(ok({ items: pendingProposals }))
    if (path === '/api/settings/ai-model') return Promise.resolve(ok({ enabled: model }))
    return Promise.resolve(ok(undefined))
  }) as never)
}

/** The path of every `GET` made so far. */
function askedPaths(): unknown[] {
  return vi.mocked(api.GET).mock.calls.map((call) => (call as unknown as [string])[0])
}

/** The `state` of every `GET /api/inbox/items` made so far. */
function askedStates(): unknown[] {
  return vi.mocked(api.GET).mock.calls.flatMap((call) => {
    const [path, init] = call as unknown as [string, { params?: { query?: { state?: unknown } } }?]
    return path === '/api/inbox/items' ? [init?.params?.query?.state] : []
  })
}

function page(tab: InboxTab = 'pending', extra: Partial<React.ComponentProps<typeof InboxPage>> = {}) {
  const onTabChange = vi.fn()
  const onItemChange = vi.fn()
  renderWithQuery(
    <InboxPage tab={tab} item={null} onTabChange={onTabChange} onItemChange={onItemChange} {...extra} />,
  )
  return { onTabChange, onItemChange }
}

function rowOf(name: string) {
  return screen.getByRole('row', { name: new RegExp(name) })
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(fetchWithRefresh).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
  auth.role = 'admin'
  viewport(1440)
})

describe('the list of «In arrivo»', () => {
  it('lists what waits (new and proposed together), newest first, with its kind, state and why', async () => {
    serving({ byState: { new: [NEW_RECEIPT, OLDER], proposed: [PROPOSED] } })
    page()

    expect(await screen.findByText('fattura-luce.pdf')).toBeInTheDocument()
    const names = screen.getAllByRole('row').slice(1).map((row) => within(row).getByText(/\.(pdf|csv)$/).textContent)
    expect(names).toEqual(['fattura-luce.pdf', 'scontrino.pdf', 'estratto.csv'])

    const proposed = rowOf('fattura-luce.pdf')
    expect(within(proposed).getByText('Fattura di acquisto')).toBeInTheDocument()
    expect(within(proposed).getByText('Da confermare')).toBeInTheDocument()
    expect(within(proposed).getByText('Proposta in attesa di una persona')).toBeInTheDocument()
    const unread = rowOf('scontrino.pdf')
    expect(within(unread).getByText('Da leggere')).toBeInTheDocument()
    expect(within(rowOf('estratto.csv')).getByText('Estratto conto')).toBeInTheDocument()
  })

  it('asks the API for each state of the tab, and for the other tabs’ states on their own tabs', async () => {
    serving({ byState: {} })
    page('set_aside')
    await screen.findByText(/Nessun documento qui/)
    const asked = askedStates()
    expect(asked).toEqual(expect.arrayContaining(['discarded', 'duplicate']))
    expect(asked).not.toContain('new')
  })

  it('moves to another tab through the router’s callback', async () => {
    serving()
    const { onTabChange } = page()
    await userEvent.click(await screen.findByRole('tab', { name: 'Archiviati' }))
    expect(onTabChange).toHaveBeenCalledWith('filed')
  })

  it('names the archive a file was unpacked from with a chip, never a nested row', async () => {
    serving({ byState: { new: [item({ id: '0199aaaa-0000-7000-8000-000000000005', original_name: 'dentro.pdf', parent_id: 'zip1' })] } })
    page()
    expect(await screen.findByText('da lotto.zip')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(2)
  })

  it('shows the amount a proposal read, and nothing for an item with none', async () => {
    serving({
      byState: { proposed: [PROPOSED], new: [NEW_RECEIPT] },
      pendingProposals: [{ id: 'p1', inbox_item_id: PROPOSED.id, proposed_fields: { total: '1464.00', currency: 'EUR' } }],
    })
    page()
    expect(await screen.findByText('1.464,00 €')).toBeInTheDocument()
    expect(within(rowOf('scontrino.pdf')).queryByText(/€/)).toBeNull()
  })

  it('says «Solo tu» on an uploader’s own item with no company yet, and not to an administrator', async () => {
    auth.role = 'collaborator'
    serving({ byState: { new: [NEW_RECEIPT] } })
    page()
    expect(await screen.findByText('Solo tu')).toBeInTheDocument()
  })

  it('does not say «Solo tu» to an administrator', async () => {
    serving({ byState: { new: [NEW_RECEIPT] } })
    page()
    await screen.findByText('scontrino.pdf')
    expect(screen.queryByText('Solo tu')).toBeNull()
  })

  it('reads «Azienda da scegliere» on a kind that has no company', async () => {
    serving({ byState: { new: [item({ original_name: 'fattura.xml', file_type: 'xml', kind: 'supplier_invoice', reason: 'Fattura di acquisto: il destinatario non è un’azienda dello spazio' })] } })
    page()
    expect(await screen.findByText('Azienda da scegliere')).toBeInTheDocument()
  })

  it('draws the loader and its sentence while it reads, in the place of the list', () => {
    vi.mocked(api.GET).mockImplementation((() => new Promise(() => {})) as never)
    page()
    expect(screen.getByRole('status', { name: 'Leggo i nuovi arrivi…' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('says what lives here, with the one action, when nothing waits', async () => {
    serving()
    page()
    expect(await screen.findByText(/Qui arrivano fatture, ricevute, estratti conto e contratti/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Carica documenti/ }).length).toBeGreaterThan(0)
  })

  it('shows the error and «Riprova» and no list under it', async () => {
    vi.mocked(api.GET).mockResolvedValue(refused(500, { detail: 'Il database non risponde.', code: 'internal', title: 'x', status: 500, instance: '/' }) as never)
    page()
    expect(await screen.findByText('Il database non risponde.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('reads fifty at a time and «Mostra altri» reads the next page', async () => {
    const many = Array.from({ length: 60 }, (_, index) =>
      item({ id: `0199aaaa-0000-7000-8000-${String(1000 - index).padStart(12, '0')}`, original_name: `doc-${index}.pdf` }),
    )
    serving({ byState: { new: many } })
    page()
    await screen.findByText('doc-0.pdf')
    expect(screen.getAllByRole('row')).toHaveLength(51)
    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(61))
  })

  it('opens the item of a row through the router’s callback, from the mouse and the keyboard', async () => {
    serving({ byState: { new: [NEW_RECEIPT] } })
    const { onItemChange } = page()
    await userEvent.click(await screen.findByText('scontrino.pdf'))
    expect(onItemChange).toHaveBeenCalledWith(NEW_RECEIPT.id)
    onItemChange.mockClear()
    rowOf('scontrino.pdf').focus()
    await userEvent.keyboard('{Enter}')
    expect(onItemChange).toHaveBeenCalledWith(NEW_RECEIPT.id)
  })

  it('draws cards, not a table, on a phone', async () => {
    viewport(390)
    serving({ byState: { new: [NEW_RECEIPT] } })
    page()
    expect(await screen.findByRole('list', { name: 'Documenti in arrivo' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })
})

describe('«Leggi i documenti in attesa»', () => {
  const UNREAD = [NEW_RECEIPT]

  it('is offered to an administrator with the model on while something waits to be read, and reads', async () => {
    serving({ byState: { new: UNREAD }, model: true, unread: UNREAD })
    vi.mocked(api.POST).mockResolvedValue(ok({ items: [NEW_RECEIPT], next_cursor: null }) as never)
    page()
    await userEvent.click(await screen.findByRole('button', { name: 'Leggi i documenti in attesa' }))
    expect(api.POST).toHaveBeenCalledWith('/api/inbox/classify', expect.anything())
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Letti 1 documento.'))
  })

  it('is not offered with the model off', async () => {
    serving({ byState: { new: UNREAD }, model: false, unread: UNREAD })
    page()
    await screen.findByText('scontrino.pdf')
    expect(screen.queryByRole('button', { name: 'Leggi i documenti in attesa' })).toBeNull()
  })

  it('is not offered when nothing waits to be read', async () => {
    serving({ byState: { new: [PROPOSED] }, model: true, unread: [] })
    page()
    await screen.findByText('fattura-luce.pdf')
    expect(screen.queryByRole('button', { name: 'Leggi i documenti in attesa' })).toBeNull()
  })

  it('is for the administrator alone: nobody else can know the model is on, so nobody else asks', async () => {
    auth.role = 'collaborator'
    serving({ byState: { new: UNREAD }, model: true, unread: UNREAD })
    page()
    await screen.findByText('scontrino.pdf')
    expect(screen.queryByRole('button', { name: 'Leggi i documenti in attesa' })).toBeNull()
    expect(askedPaths()).not.toContain('/api/settings/ai-model')
  })
})

describe('upload from the page', () => {
  const pdf = (name = 'a.pdf') => new File(['%PDF-1.7'], name, { type: 'application/pdf' })
  const created = (names: string[]) =>
    new Response(JSON.stringify({ items: names.map((name) => item({ original_name: name })) }), { status: 201 })

  it('sends each chosen file in a request of its own and says how many documents arrived', async () => {
    serving()
    vi.mocked(fetchWithRefresh).mockImplementation(async () => created(['x']))
    page()
    const input = await screen.findByLabelText('Scegli i file da caricare')
    await userEvent.upload(input, [pdf('a.pdf'), pdf('b.pdf')])

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('2 documenti caricati in In arrivo.'))
    expect(fetchWithRefresh).toHaveBeenCalledTimes(2)
    for (const call of vi.mocked(fetchWithRefresh).mock.calls) {
      expect(call[0]).toBe('/api/inbox/upload')
      expect((call[1]?.body as FormData).getAll('file')).toHaveLength(1)
    }
  })

  it('takes a file dropped anywhere on the page', async () => {
    serving()
    vi.mocked(fetchWithRefresh).mockImplementation(async () => created(['x']))
    page()
    const header = await screen.findByRole('heading', { name: 'In arrivo' })
    fireEvent.drop(header, { dataTransfer: { files: [pdf('trascinato.pdf')], types: ['Files'] } })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('1 documento caricato in In arrivo.'))
    expect(((vi.mocked(fetchWithRefresh).mock.calls[0]?.[1]?.body) as FormData).get('file')).toHaveProperty('name', 'trascinato.pdf')
  })

  it('draws a row at once, with «Lo leggo…», until the item is made', async () => {
    serving()
    let release: (response: Response) => void = () => {}
    vi.mocked(fetchWithRefresh).mockImplementation(
      () => new Promise<Response>((resolve) => (release = resolve)),
    )
    page()
    await userEvent.upload(await screen.findByLabelText('Scegli i file da caricare'), pdf('lento.pdf'))
    const row = await screen.findByRole('status', { name: '' })
    expect(within(row).getByText('lento.pdf')).toBeInTheDocument()
    expect(within(row).getByText('Lo leggo…')).toBeInTheDocument()
    release(created(['lento.pdf']))
    await waitFor(() => expect(screen.queryByText('Lo leggo…')).toBeNull())
  })

  it('keeps a refused file as a red row with its reason until it is dismissed, never only a toast', async () => {
    serving()
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Formato non accettato.', code: 'validation_failed', title: 'x', status: 422, instance: '/' }), { status: 422 }),
    )
    page()
    await userEvent.upload(await screen.findByLabelText('Scegli i file da caricare'), pdf('strano.pdf'))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('strano.pdf')).toBeInTheDocument()
    expect(within(alert).getByText('Non caricato: formato non accettato.')).toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /Chiudi l’avviso su strano\.pdf/ }))
    expect(screen.queryByText('strano.pdf')).toBeNull()
  })

  it('refuses a file over 100 MiB without sending it', async () => {
    serving()
    page()
    const big = pdf('enorme.pdf')
    Object.defineProperty(big, 'size', { value: 101 * 1024 * 1024 })
    await userEvent.upload(await screen.findByLabelText('Scegli i file da caricare'), big)
    expect(await screen.findByText('Non caricato: supera i 100 MiB.')).toBeInTheDocument()
    expect(fetchWithRefresh).not.toHaveBeenCalled()
  })

  it('offers no upload to a readonly member', async () => {
    auth.role = 'readonly'
    serving()
    page()
    await screen.findByText(/Qui arrivano/)
    expect(screen.queryByRole('button', { name: /Carica documenti/ })).toBeNull()
    expect(screen.queryByLabelText('Scegli i file da caricare')).toBeNull()
  })
})

describe('the bulk bar', () => {
  it('discards the selection with one reason, each item on its own request', async () => {
    serving({ byState: { new: [NEW_RECEIPT, OLDER] } })
    vi.mocked(api.POST).mockResolvedValue(ok(item({ state: 'discarded' })) as never)
    page()
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Scegli scontrino.pdf' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Scegli estratto.csv' }))
    expect(screen.getByRole('toolbar')).toHaveTextContent('2 scelti')

    await userEvent.click(within(screen.getByRole('toolbar')).getByRole('button', { name: 'Scarta…' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = within(dialog).getByRole('button', { name: 'Scarta' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Motivo'), 'Non sono nostri')
    await userEvent.click(confirm)

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2))
    expect(api.POST).toHaveBeenCalledWith(
      '/api/inbox/items/{item_id}/discard',
      expect.objectContaining({ body: { reason: 'Non sono nostri' } }),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('2 documenti scartati.'))
  })

  it('keeps the dialog, the selection and the server’s sentence when every item is refused', async () => {
    serving({ byState: { new: [NEW_RECEIPT] } })
    vi.mocked(api.POST).mockResolvedValue(
      refused(409, { detail: 'inbox_item: non si scarta: l’elemento è già registrato', code: 'conflict', title: 'x', status: 409, instance: '/' }) as never,
    )
    page()
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Scegli scontrino.pdf' }))
    await userEvent.click(within(screen.getByRole('toolbar')).getByRole('button', { name: 'Scarta…' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Motivo'), 'x')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Scarta' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('già registrato')
    expect(dialog).toHaveTextContent('Scarta il documento')
    expect(screen.getByRole('toolbar', { hidden: true })).toHaveTextContent('1 scelto')
  })

  it('has no checkboxes and no bar on a tab where nothing waits', async () => {
    serving({ byState: { discarded: [item({ state: 'discarded', reason: 'Non è nostro' })] } })
    page('set_aside')
    await screen.findByText('Non è nostro')
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})
