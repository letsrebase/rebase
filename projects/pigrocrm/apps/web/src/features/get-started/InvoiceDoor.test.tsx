/**
 * The invoice door (spec 2026-09-16 §6, REB-224, «prima il cliente»): the customer
 * first, then one PDF filed as a `fattura` document of that customer, then the handoff
 * with the document's id, and «Registrata» once an invoice has it as its original. The
 * API client is stubbed by path, the multipart upload through `fetch`, `useAuth` by role;
 * every test gets a new `QueryClient`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { InvoiceDoor } from './InvoiceDoor'
import { handoffKey } from './invoiceHandoff'
import { invoicePrompt } from './prompts'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

const auth: { user: { id: string; email: string; nome: string; ruolo: 'admin' | 'collaboratore' | 'readonly'; attivo: boolean } } = {
  user: { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin', attivo: true },
}
vi.mock('@/lib/auth', async () => {
  const { can } = await vi.importActual<typeof import('@/lib/permissions')>('@/lib/permissions')
  return {
    useAuth: () => ({ user: auth.user, isLoading: false, login: vi.fn(), logout: vi.fn(), enterWithLink: vi.fn() }),
    useCan: (action: string) => can(auth.user.ruolo, action),
    useCanWrite: () => auth.user.ruolo !== 'readonly',
  }
})

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    params,
    children,
    ...rest
  }: { to: string; params?: Record<string, string>; children: React.ReactNode } & Record<string, unknown>) => (
    <a href={Object.entries(params ?? {}).reduce((href, [k, v]) => href.replace(`$${k}`, v), to)} {...rest}>
      {children}
    </a>
  ),
  useBlocker: vi.fn(),
}))

const EMPTY_PAGE = { items: [], next_cursor: null }
const CUSTOMER = { id: 'c-new', ragione_sociale: 'Officina Verdi S.r.l.' }
const DOCUMENT = { id: 'doc-1', customer_id: 'c-new', tipo: 'fattura', titolo: 'fattura-12.pdf' }
const INVOICE = { id: 'inv-1', customer_id: 'c-new', anno: 2026, numero: 12, pdf_document_id: 'doc-1' }
const HANDOFF = { documentId: 'doc-1', customerId: 'c-new', customerName: 'Officina Verdi S.r.l.' }

type Answer = { data: unknown } | { status: number; detail?: string }
function reply(answer: Answer) {
  return 'data' in answer
    ? Promise.resolve({ data: answer.data, response: new Response(null, { status: 200 }) })
    : Promise.resolve({
        error: { detail: answer.detail ?? 'boom' },
        response: new Response(null, { status: answer.status }),
      })
}

/** GET by path; the invoice list and the space settings are the ones a test varies. */
function gets(overrides: Record<string, Answer> = {}) {
  const defaults: Record<string, Answer> = {
    '/api/customers': { data: EMPTY_PAGE },
    '/api/documents/{document_id}': { data: DOCUMENT },
    '/api/invoices': { data: EMPTY_PAGE },
    '/api/settings/space': { data: { mcp_full_access: true } },
  }
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    reply(overrides[path] ?? defaults[path] ?? { data: null })) as never)
}

function posts(overrides: Record<string, Answer> = {}) {
  const defaults: Record<string, Answer> = {
    '/api/customers': { data: CUSTOMER },
    '/api/documents': { data: DOCUMENT },
  }
  vi.mocked(api.POST).mockImplementation(((path: string) =>
    reply(overrides[path] ?? defaults[path] ?? { data: null })) as never)
}

const uploads = vi.fn<(url: string, init?: RequestInit) => Response>()

function renderDoor({ assistantConnected = true } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <InvoiceDoor assistantConnected={assistantConnected} />
    </QueryClientProvider>,
  )
}

function pdf(name = 'fattura-12.pdf', type = 'application/pdf') {
  return new File(['%PDF-1.7'], name, { type })
}

function drop(...files: File[]) {
  fireEvent.drop(screen.getByTestId('upload-dropzone'), { dataTransfer: { files } })
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.DELETE).mockReset()
  gets()
  posts()
  uploads.mockReset()
  uploads.mockImplementation(() => new Response(JSON.stringify({ numero: 1 }), { status: 201 }))
  vi.spyOn(globalThis, 'fetch').mockImplementation(((url: string, init?: RequestInit) =>
    Promise.resolve(uploads(url, init))) as never)
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
  window.localStorage.clear()
})

afterEach(() => {
  auth.user = { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin', attivo: true }
  vi.restoreAllMocks()
})

describe('the invoice door, first the customer', () => {
  async function create() {
    const button = screen.getByRole('button', { name: 'Crea il cliente e vai avanti' })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
  }

  it('on an empty space creates the new customer by name, as Clienti does', async () => {
    renderDoor()
    const door = within(screen.getByRole('region', { name: 'Carica l’ultima fattura che hai emesso' }))
    expect(await door.findByText('A chi l’hai emessa?')).toBeInTheDocument()
    expect(door.getByRole('button', { name: 'Crea il cliente e vai avanti' })).toBeDisabled()
    await userEvent.type(door.getByLabelText('Ragione sociale del cliente'), '  Officina Verdi S.r.l. ')
    await create()
    expect(api.POST).toHaveBeenCalledWith('/api/customers', { body: { ragione_sociale: 'Officina Verdi S.r.l.' } })
    expect(await door.findByText('Officina Verdi S.r.l.')).toBeInTheDocument()
    expect(door.getByTestId('upload-dropzone')).toBeInTheDocument()
  })

  it('finds a customer already on file by searching, in a space of any size, and creates nothing', async () => {
    gets({
      '/api/customers': {
        data: { items: [{ id: 'c-old', ragione_sociale: 'ACME Srl', partita_iva: '01234567890' }], next_cursor: null },
      },
    })
    renderDoor()
    await userEvent.type(await screen.findByLabelText('Ragione sociale del cliente'), 'acm')
    await userEvent.click(await screen.findByRole('button', { name: 'ACME Srl · P.IVA 01234567890' }))
    expect(await screen.findByText('ACME Srl')).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/customers', { params: { query: { search: 'acm', limit: 8 } } })
    expect(api.POST).not.toHaveBeenCalledWith('/api/customers', expect.anything())
  })

  it('uses the customer on file when the typed name is exactly theirs, instead of a duplicate', async () => {
    gets({ '/api/customers': { data: { items: [{ id: 'c-old', ragione_sociale: 'ACME Srl' }], next_cursor: null } } })
    renderDoor()
    await userEvent.type(await screen.findByLabelText('Ragione sociale del cliente'), 'acme srl')
    const use = await screen.findByRole('button', { name: 'Usa ACME Srl' })
    await waitFor(() => expect(use).toBeEnabled())
    await userEvent.click(use)
    expect(await screen.findByTestId('upload-dropzone')).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalledWith('/api/customers', expect.anything())
  })

  it('uses a customer named exactly so even when the suggestions do not show it, instead of a duplicate', async () => {
    const partial = Array.from({ length: 8 }, (_, i) => ({ id: `p${i}`, ragione_sociale: `ACME Srl filiale ${i}` }))
    vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: { limit?: number; cursor?: string } } }) => {
      if (path !== '/api/customers') return reply({ data: null })
      const query = init?.params?.query ?? {}
      if (query.limit === 8) return reply({ data: { items: partial, next_cursor: 'more' } })
      // The whole search, page by page: the exact name is on the second page.
      return query.cursor === undefined
        ? reply({ data: { items: partial, next_cursor: 'page-2' } })
        : reply({ data: { items: [{ id: 'c-old', ragione_sociale: 'ACME Srl' }], next_cursor: null } })
    }) as never)
    renderDoor()
    await userEvent.type(await screen.findByLabelText('Ragione sociale del cliente'), 'ACME Srl')
    await create()
    expect(await screen.findByTestId('upload-dropzone')).toBeInTheDocument()
    expect(screen.getByText('ACME Srl')).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalledWith('/api/customers', expect.anything())
  })

  it('holds the name while it is looked up, so the customer chosen is the one on screen', async () => {
    let release: (value: unknown) => void = () => {}
    vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: { limit?: number } } }) => {
      if (path !== '/api/customers') return reply({ data: null })
      if (init?.params?.query?.limit === 8) return reply({ data: EMPTY_PAGE })
      return new Promise((resolve) => {
        release = resolve
      })
    }) as never)
    renderDoor()
    const field = await screen.findByLabelText('Ragione sociale del cliente')
    await userEvent.type(field, 'Officina')
    await create()
    await waitFor(() => expect(field).toHaveAttribute('readonly'))
    await userEvent.type(field, ' Rossi')
    expect(field).toHaveValue('Officina')
    release({ data: EMPTY_PAGE, response: new Response(null, { status: 200 }) })
    expect(await screen.findByTestId('upload-dropzone')).toBeInTheDocument()
    expect(api.POST).toHaveBeenCalledWith('/api/customers', { body: { ragione_sociale: 'Officina' } })
  })

  it('still takes a new name when the customers on file cannot be searched', async () => {
    gets({ '/api/customers': { status: 500 } })
    renderDoor()
    await userEvent.type(await screen.findByLabelText('Ragione sociale del cliente'), 'Officina')
    expect(await screen.findByText(/Non riesco a cercare tra i clienti/)).toBeInTheDocument()
    await create()
    expect(api.POST).toHaveBeenCalledWith('/api/customers', { body: { ragione_sociale: 'Officina' } })
  })

  it('says why a new customer was refused, in the server’s words', async () => {
    posts({ '/api/customers': { status: 422, detail: 'ragione_sociale: già usata' } })
    renderDoor()
    await userEvent.type(await screen.findByLabelText('Ragione sociale del cliente'), 'Officina')
    await create()
    expect(await screen.findByRole('alert')).toHaveTextContent('già usata')
    expect(screen.queryByTestId('upload-dropzone')).toBeNull()
  })
})

describe('the invoice door, then the PDF', () => {
  async function toUpload() {
    renderDoor({ assistantConnected: false })
    await userEvent.type(await screen.findByLabelText('Ragione sociale del cliente'), 'Officina Verdi S.r.l.')
    const button = screen.getByRole('button', { name: 'Crea il cliente e vai avanti' })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    await screen.findByTestId('upload-dropzone')
  }

  it('files one PDF as a fattura of that customer, uploads it, and hands it to the assistant', async () => {
    await toUpload()
    drop(pdf())
    expect(await screen.findByText(invoicePrompt(HANDOFF))).toBeInTheDocument()
    expect(api.POST).toHaveBeenCalledWith('/api/documents', {
      body: { customer_id: 'c-new', tipo: 'fattura', titolo: 'fattura-12.pdf', custom_fields: {} },
    })
    expect(uploads).toHaveBeenCalledWith('/api/documents/doc-1/versions', expect.anything())
    // No token yet: the connection sits next to the prompt.
    expect(screen.getByText('Prima collega l’assistente')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Crea il token' })).toBeInTheDocument()
    // Remembered, so «Primi passi» still shows the handoff on the next visit.
    expect(JSON.parse(window.localStorage.getItem(handoffKey('u1')) ?? '{}')).toEqual(HANDOFF)
  })

  it('refuses anything but a PDF, and more than one file, before a request is made', async () => {
    await toUpload()
    drop(pdf('fattura.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Serve il PDF della fattura')
    drop(pdf('a.pdf'), pdf('b.pdf'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Una fattura alla volta')
    expect(api.POST).not.toHaveBeenCalledWith('/api/documents', expect.anything())
  })

  it('sends a .pdf the system left untyped as a PDF, not as the octet-stream the server refuses', async () => {
    await toUpload()
    drop(pdf('fattura.pdf', ''))
    await screen.findByText(invoicePrompt(HANDOFF))
    const init = uploads.mock.calls[0]?.[1]
    expect(((init?.body as FormData).get('file') as File).type).toBe('application/pdf')
  })

  it('files one document however many times the PDF is dropped while the first is on its way', async () => {
    await toUpload()
    let release: (value: unknown) => void = () => {}
    vi.mocked(api.POST).mockImplementation(((path: string) =>
      path === '/api/documents'
        ? new Promise((resolve) => {
            release = resolve
          })
        : reply({ data: CUSTOMER })) as never)
    drop(pdf())
    await waitFor(() => expect(api.POST).toHaveBeenCalledWith('/api/documents', expect.anything()))
    drop(pdf())
    drop(pdf())
    release({ data: DOCUMENT, response: new Response(null, { status: 201 }) })
    expect(await screen.findByText(invoicePrompt(HANDOFF))).toBeInTheDocument()
    expect(vi.mocked(api.POST).mock.calls.filter((call) => (call as unknown[])[0] === '/api/documents')).toHaveLength(1)
    expect(uploads).toHaveBeenCalledTimes(1)
  })

  it('removes the empty document when its file fails to upload, and the next drop files a fresh one', async () => {
    await toUpload()
    vi.mocked(api.DELETE).mockResolvedValue({ data: undefined, response: new Response(null, { status: 204 }) } as never)
    uploads.mockImplementationOnce(() => new Response(JSON.stringify({ detail: 'disco pieno' }), { status: 500 }))
    drop(pdf())
    expect(await screen.findByRole('alert')).toHaveTextContent('disco pieno')
    await waitFor(() =>
      expect(api.DELETE).toHaveBeenCalledWith('/api/documents/{document_id}', { params: { path: { document_id: 'doc-1' } } }),
    )
    // Nothing is filed any more, so the customer can be changed again.
    expect(await screen.findByRole('button', { name: 'Cambia cliente' })).toBeInTheDocument()
    drop(pdf())
    expect(await screen.findByText(invoicePrompt(HANDOFF))).toBeInTheDocument()
    expect(vi.mocked(api.POST).mock.calls.filter((call) => (call as unknown[])[0] === '/api/documents')).toHaveLength(2)
  })

  it('retries onto the same document when even its removal fails', async () => {
    await toUpload()
    vi.mocked(api.DELETE).mockResolvedValue({
      error: { detail: 'boom' },
      response: new Response(null, { status: 503 }),
    } as never)
    uploads.mockImplementationOnce(() => new Response(JSON.stringify({ detail: 'disco pieno' }), { status: 500 }))
    drop(pdf())
    expect(await screen.findByRole('alert')).toHaveTextContent('disco pieno')
    await waitFor(() => expect(api.DELETE).toHaveBeenCalled())
    // The document is still filed under this customer: it cannot be changed now.
    expect(screen.queryByRole('button', { name: 'Cambia cliente' })).toBeNull()
    drop(pdf())
    expect(await screen.findByText(invoicePrompt(HANDOFF))).toBeInTheDocument()
    expect(vi.mocked(api.POST).mock.calls.filter((call) => (call as unknown[])[0] === '/api/documents')).toHaveLength(1)
    expect(uploads).toHaveBeenCalledTimes(2)
  })
})

describe('the invoice door, the handoff', () => {
  beforeEach(() => {
    window.localStorage.setItem(handoffKey('u1'), JSON.stringify(HANDOFF))
  })

  it('picks the handoff up again on a later visit, with the prompt and no connection when a token exists', async () => {
    renderDoor({ assistantConnected: true })
    expect(await screen.findByText(invoicePrompt(HANDOFF))).toBeInTheDocument()
    expect(screen.queryByText('Prima collega l’assistente')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Copia il prompt' }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(invoicePrompt(HANDOFF))
  })

  it('says «Registrata» with the link once an invoice of that customer has the document as its original', async () => {
    gets({ '/api/invoices': { data: { items: [{ ...INVOICE, pdf_document_id: 'altro' }, INVOICE], next_cursor: null } } })
    renderDoor()
    expect(await screen.findByText('Registrata')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Registrata')
    expect(screen.getByRole('link', { name: /Apri la fattura 12\/2026 di Officina Verdi/ })).toHaveAttribute(
      'href',
      '/app/invoices/inv-1',
    )
    expect(api.GET).toHaveBeenCalledWith('/api/invoices', { params: { query: { customer_id: 'c-new', limit: 200 } } })
    expect(screen.queryByText(invoicePrompt(HANDOFF))).toBeNull()
  })

  it('looks again when the door mounts again, rather than trust a cached «not yet»', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } })
    const tree = (
      <QueryClientProvider client={client}>
        <InvoiceDoor assistantConnected />
      </QueryClientProvider>
    )
    const { unmount } = render(tree)
    await screen.findByText(invoicePrompt(HANDOFF))
    unmount()
    // Meanwhile the assistant registered it.
    gets({ '/api/invoices': { data: { items: [INVOICE], next_cursor: null } } })
    render(tree)
    expect(await screen.findByText('Registrata')).toBeInTheDocument()
  })

  it('tells an admin the assistant needs the full access switch while it is off', async () => {
    gets({ '/api/settings/space': { data: { mcp_full_access: false } } })
    renderDoor()
    const note = await screen.findByRole('note')
    expect(note).toHaveTextContent('accesso completo per i token dell’agente')
    expect(note).toHaveTextContent('ricollega l’assistente')
    expect(within(note).getByRole('link', { name: 'Impostazioni → Spazio' })).toHaveAttribute('href', '/app/settings/space')
  })

  it('shows the switch note when the settings cannot be read, rather than assume it is on', async () => {
    gets({ '/api/settings/space': { status: 500 } })
    renderDoor()
    expect(await screen.findByRole('note')).toHaveTextContent('accesso completo per i token dell’agente')
  })

  it('says nothing about the switch once it is on', async () => {
    renderDoor()
    await screen.findByText(invoicePrompt(HANDOFF))
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/settings/space'))
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('starts over, and forgets the handoff, on «Carica un’altra fattura»', async () => {
    renderDoor()
    await screen.findByText(invoicePrompt(HANDOFF))
    await userEvent.click(screen.getByRole('button', { name: 'Carica un’altra fattura' }))
    expect(await screen.findByText('A chi l’hai emessa?')).toBeInTheDocument()
    expect(window.localStorage.getItem(handoffKey('u1'))).toBeNull()
  })

  it('keeps the prompt when the document read merely failed: that says nothing about the file', async () => {
    gets({ '/api/documents/{document_id}': { status: 503 } })
    renderDoor()
    expect(await screen.findByText(invoicePrompt(HANDOFF))).toBeInTheDocument()
    expect(screen.queryByText(/non è più tra i documenti/)).toBeNull()
  })

  it('never shows the previous invoice as «Registrata» for the next upload to the same customer', async () => {
    gets({ '/api/invoices': { data: { items: [INVOICE], next_cursor: null } } })
    renderDoor()
    expect(await screen.findByText('Registrata')).toBeInTheDocument()
    // The next read of the invoices fails: whatever is shown next can only come from
    // the cache, which must not be the previous document's.
    gets({ '/api/invoices': { status: 500 } })
    posts({ '/api/documents': { data: { ...DOCUMENT, id: 'doc-2' } } })
    await userEvent.click(screen.getByRole('button', { name: 'Carica un’altra fattura' }))
    await userEvent.type(await screen.findByLabelText('Ragione sociale del cliente'), 'Officina Verdi S.r.l.')
    const button = screen.getByRole('button', { name: 'Crea il cliente e vai avanti' })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    await screen.findByTestId('upload-dropzone')
    drop(pdf())
    const second = { ...HANDOFF, documentId: 'doc-2' }
    expect(await screen.findByText(invoicePrompt(second))).toBeInTheDocument()
    expect(screen.queryByText('Registrata')).toBeNull()
  })

  it('does not prompt about a document that is gone', async () => {
    gets({ '/api/documents/{document_id}': { status: 404, detail: 'document non trovato' } })
    renderDoor()
    expect(await screen.findByText('La fattura caricata non è più tra i documenti.')).toBeInTheDocument()
    expect(screen.queryByText(invoicePrompt(HANDOFF))).toBeNull()
  })

  it('ignores a remembered handoff it cannot read', async () => {
    window.localStorage.setItem(handoffKey('u1'), '{"documentId": 3}')
    renderDoor()
    expect(await screen.findByText('A chi l’hai emessa?')).toBeInTheDocument()
  })
})

describe('the invoice door, by role', () => {
  it.each(['collaboratore', 'readonly'] as const)(
    'is the admin’s: a %s reads who registers an issued invoice, and gets no upload',
    async (ruolo) => {
      auth.user.ruolo = ruolo
      renderDoor()
      expect(screen.getByText(/passo dell’amministratore dello spazio/)).toBeInTheDocument()
      expect(screen.queryByText('A chi l’hai emessa?')).toBeNull()
      expect(api.GET).not.toHaveBeenCalledWith('/api/customers', expect.anything())
    },
  )
})
