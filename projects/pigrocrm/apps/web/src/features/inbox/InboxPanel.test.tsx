import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { InboxPanel } from './InboxPanel'
import { ME, detail, item, ok, refused, renderWithQuery } from './testing'
import type { InboxItemDetail } from './labels'

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

const PROPOSAL = {
  id: 'p1',
  document_id: null,
  inbox_item_id: 'i1',
  owner_user_id: null,
  contract_id: null,
  target_type: 'supplier_invoice',
  proposed_fields: { issuer_name: 'Enel Energia', number: 'FT-9', issue_date: '2026-11-02', total: '1464.00', currency: 'EUR' },
  excerpt: 'Totale 1.464,00',
  excerpt_type: 'quoted',
  confidence: '0.91',
  confidence_reason: null,
  status: 'pending',
  accepted_fields: null,
  result_id: null,
  decided_by: null,
  decided_at: null,
  created_at: '2026-11-03T10:01:00Z',
  updated_at: '2026-11-03T10:01:00Z',
}

interface Serving {
  shown: InboxItemDetail
  proposalActor?: string
  copies?: unknown[]
  candidates?: unknown[]
}

function serving({ shown, proposalActor = 'user', copies = [], candidates = [] }: Serving) {
  vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: Record<string, unknown> } }) => {
    if (path === '/api/inbox/items/{item_id}') return Promise.resolve(ok(shown))
    if (path === '/api/inbox/items') {
      return Promise.resolve(ok({ items: init?.params?.query?.state === 'duplicate' ? copies : candidates, next_cursor: null }))
    }
    if (path === '/api/proposals/{proposal_id}') return Promise.resolve(ok(PROPOSAL))
    if (path === '/api/proposals/{proposal_id}/timeline') {
      return Promise.resolve(ok([{ id: 'a1', kind: 'created', actor_type: proposalActor, payload: {}, occurred_at: '2026-11-03T10:01:00Z' }]))
    }
    if (path === '/api/settings/ai-model') return Promise.resolve(ok({ enabled: true }))
    if (path === '/api/inbox/items/{item_id}/issued-review') {
      return Promise.resolve(ok({ lines: [{ file: 'f.xml', sha256: 's', outcome: 'ready', reason: null, party_key: null, existing_organization_id: null, invoice: { number: '12', issue_date: '2026-11-01', sdi_document_type: 'invoice', total: '100.00', customer: { legal_name: 'Cliente Srl' } } }] }))
    }
    return Promise.resolve(ok(undefined))
  }) as never)
}

function panel(id = 'i1') {
  const onOpen = vi.fn()
  const onClose = vi.fn()
  renderWithQuery(<InboxPanel id={id} onOpen={onOpen} onClose={onClose} />)
  return { onOpen, onClose }
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
  auth.role = 'admin'
})

describe('what the panel shows of an item', () => {
  it('shows the file, where it came from, what the model read with its tag, and links to the proposal', async () => {
    serving({
      shown: detail({ id: 'i1', original_name: 'fattura-luce.pdf', state: 'proposed', kind: 'supplier_invoice', proposal_id: 'p1', legal_entity_id: null }),
    })
    panel()

    expect(await screen.findByRole('heading', { name: 'fattura-luce.pdf' })).toBeInTheDocument()
    expect(screen.getByText('PDF · 12,1 kB')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apri il file/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Scarica il file/ })).toBeInTheDocument()
    const origin = screen.getByRole('region', { name: 'Da dove arriva' })
    expect(within(origin).getByText('Tu')).toBeInTheDocument()
    expect(within(origin).getByText('03/11/2026')).toBeInTheDocument()

    const read = screen.getByRole('region', { name: 'Cosa è stato letto' })
    expect(await within(read).findByText('Letto dal modello')).toBeInTheDocument()
    expect(within(read).getByText('Enel Energia')).toBeInTheDocument()
    expect(within(read).getByText('1.464,00 €')).toBeInTheDocument()
    expect(within(read).getByRole('link', { name: 'Apri la proposta' })).toHaveAttribute('href', '/app/proposals/p1')
  })

  it('says «Letto dal tuo agente» when the proposal was made over MCP', async () => {
    serving({ shown: detail({ id: 'i1', state: 'proposed', kind: 'supplier_invoice', proposal_id: 'p1' }), proposalActor: 'mcp' })
    panel()
    expect(await screen.findByText('Letto dal tuo agente')).toBeInTheDocument()
  })

  it('shows what a FatturaPA states, as certain data', async () => {
    serving({
      shown: detail({
        id: 'i1',
        original_name: 'fattura.xml',
        file_type: 'xml',
        kind: 'supplier_invoice',
        parsed_fields: {
          invoices: [{ number: '77', issue_date: '2026-10-20', currency: 'EUR', supplier: { name: 'Fornitore Spa' }, customer: { name: 'Noi Srl' }, total: '244.00' }],
        },
      }),
    })
    panel()
    expect(await screen.findByText('Dal file: dato certo')).toBeInTheDocument()
    expect(screen.getByText('Fornitore Spa')).toBeInTheDocument()
    expect(screen.getByText('244,00 €')).toBeInTheDocument()
    expect(screen.getByText('Fattura elettronica (XML) · 12,1 kB')).toBeInTheDocument()
  })

  it('says the file is not here once it was filed, and offers nothing to do', async () => {
    serving({ shown: detail({ id: 'i1', state: 'filed', has_file: false, kind: 'sales_invoice', reason: 'Fattura emessa importata nel registro' }) })
    panel()
    expect(await screen.findByText('Il file è nel registro, dove è stato archiviato.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Scarta/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Scarica il file/ })).toBeNull()
  })

  it('lists the other times the same file arrived', async () => {
    serving({
      shown: detail({ id: 'i1' }),
      copies: [item({ id: 'c1', original_name: 'copia.pdf', state: 'duplicate', duplicate_of_id: 'i1', created_at: '2026-11-04T09:00:00Z' }), item({ id: 'c2', state: 'duplicate', duplicate_of_id: 'other' })],
    })
    const { onOpen } = panel()
    const copies = await screen.findByRole('region', { name: 'Arrivata anche' })
    expect(within(copies).getAllByRole('listitem')).toHaveLength(1)
    expect(copies).toHaveTextContent('04/11/2026, stesso file')
    await userEvent.click(within(copies).getByRole('button', { name: 'copia.pdf' }))
    expect(onOpen).toHaveBeenCalledWith('c1')
  })

  it('says it could not read an item it is not allowed to see', async () => {
    vi.mocked(api.GET).mockResolvedValue(refused(404, { detail: 'inbox_item non trovato', code: 'not_found', title: 'x', status: 404, instance: '/' }) as never)
    const { onClose } = panel('nascosto')
    expect(await screen.findByText('inbox_item non trovato')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Chiudi' }))
    expect(onClose).toHaveBeenCalled()
  })
})

describe('the acts on an item', () => {
  it('discards with a reason, and says it did', async () => {
    serving({ shown: detail({ id: 'i1' }) })
    vi.mocked(api.POST).mockResolvedValue(ok(item({ state: 'discarded' })) as never)
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Scarta…' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Motivo'), 'Non è nostro')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Scarta' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/inbox/items/{item_id}/discard',
        expect.objectContaining({ body: { reason: 'Non è nostro' }, params: { path: { item_id: 'i1' } } }),
      ),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Documento scartato.'))
  })

  it('says that a proposal waiting on the document is rejected too, before it happens', async () => {
    serving({ shown: detail({ id: 'i1', state: 'proposed', kind: 'receipt', proposal_id: 'p1' }) })
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Scarta…' }))
    expect(await screen.findByRole('note')).toHaveTextContent('proposta in attesa')
  })

  it('keeps the dialog open with the server’s sentence when the item was decided meanwhile', async () => {
    serving({ shown: detail({ id: 'i1' }) })
    vi.mocked(api.POST).mockResolvedValue(refused(409, { detail: 'inbox_item: non si scarta: l’elemento è già registrato', code: 'conflict', title: 'x', status: 409, instance: '/' }) as never)
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Scarta…' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Motivo'), 'x')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Scarta' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('già registrato')
  })

  it('marks it as the copy of another file', async () => {
    serving({ shown: detail({ id: 'i1' }), candidates: [item({ id: 'o1', original_name: 'originale.pdf' }), item({ id: 'i1' })] })
    vi.mocked(api.POST).mockResolvedValue(ok(item({ state: 'duplicate' })) as never)
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Segna come doppione…' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('combobox', { name: 'Copia di' }))
    await userEvent.click(await screen.findByRole('option', { name: 'originale.pdf' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Segna come doppione' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/inbox/items/{item_id}/duplicate',
        expect.objectContaining({ body: { original_id: 'o1' } }),
      ),
    )
  })

  it('says what a file is, offering every kind but the archive', async () => {
    serving({ shown: detail({ id: 'i1' }) })
    vi.mocked(api.POST).mockResolvedValue(ok(item({ kind: 'receipt' })) as never)
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Indica il tipo…' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('combobox', { name: 'Che cos’è' }))
    const options = (await screen.findAllByRole('option')).map((option) => option.textContent)
    expect(options).not.toContain('Archivio')
    // A PDF is no sales invoice: that one is an XML's.
    expect(options).not.toContain('Fattura emessa')
    await userEvent.click(screen.getByRole('option', { name: 'Ricevuta' }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Salva' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/inbox/items/{item_id}/kind',
        expect.objectContaining({ body: { kind: 'receipt', legal_entity_id: undefined } }),
      ),
    )
  })

  it('does not let a kind a FatturaPA decided be changed', async () => {
    serving({ shown: detail({ id: 'i1', file_type: 'xml', kind: 'supplier_invoice', parsed_fields: { invoices: [] } }) })
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Cambia tipo…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('combobox', { name: 'Che cos’è' })).toBeDisabled()
    expect(within(dialog).getByText(/dati certi: non si cambia/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Salva' })).toBeDisabled()
  })

  it('reads one item with the model when it is on, for the administrator who can know', async () => {
    serving({ shown: detail({ id: 'i1' }) })
    vi.mocked(api.POST).mockResolvedValue(ok(item({ state: 'proposed', kind: 'receipt' })) as never)
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Leggi con il modello' }))
    expect(api.POST).toHaveBeenCalledWith('/api/inbox/items/{item_id}/classify', { params: { path: { item_id: 'i1' } } })
  })

  it('offers a collaborator the acts but not the model’s reading, which only an administrator can know is on', async () => {
    auth.role = 'collaborator'
    serving({ shown: detail({ id: 'i1' }) })
    panel()
    expect(await screen.findByRole('button', { name: 'Scarta…' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Leggi con il modello' })).toBeNull()
  })

  it('offers a readonly member nothing to do', async () => {
    auth.role = 'readonly'
    serving({ shown: detail({ id: 'i1' }) })
    panel()
    await screen.findByRole('heading', { name: 'scontrino-trattoria.pdf' })
    expect(screen.queryByRole('button', { name: /Scarta|Indica il tipo|doppione/ })).toBeNull()
  })

  it('names the visibility of an item with no company, to the person it reads for', async () => {
    auth.role = 'collaborator'
    serving({ shown: detail({ id: 'i1', uploaded_by_id: ME }) })
    panel()
    expect(await screen.findByText('Visibile a te e agli amministratori finché non ha un’azienda')).toBeInTheDocument()
  })
})

describe('a sales invoice waiting for a person’s confirmation', () => {
  const SALES = detail({ id: 'i1', original_name: 'emessa.xml', file_type: 'xml', kind: 'sales_invoice', legal_entity_id: 'le1', parsed_fields: { invoices: [] } })

  it('offers the issued-invoice review to an administrator, writes nothing until «Importa», then confirms', async () => {
    serving({ shown: SALES })
    vi.mocked(api.POST).mockResolvedValue(ok({ lines: [{ file: 'f.xml', sha256: 's', outcome: 'imported', reason: null, party_key: null, invoice: null, legal_entity_id: 'le1' }] }) as never)
    panel()
    await userEvent.click(await screen.findByRole('button', { name: 'Rivedi e importa…' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('Cliente Srl')).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Importa 1 fattura' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/inbox/items/{item_id}/issued-import',
        expect.objectContaining({ params: { path: { item_id: 'i1' } }, body: { organizations: {} } }),
      ),
    )
    expect(await within(dialog).findByText('Importate')).toBeInTheDocument()
  })

  it('does not offer it to someone who may not confirm an import', async () => {
    auth.role = 'collaborator'
    serving({ shown: SALES })
    panel()
    await screen.findByRole('button', { name: 'Scarta…' })
    expect(screen.queryByRole('button', { name: 'Rivedi e importa…' })).toBeNull()
  })
})
