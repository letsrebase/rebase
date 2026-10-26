import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ContractEmailSection } from './ContractEmailSection'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }))
vi.mock('@rebase/ui/sonner', () => ({ toast }))
vi.mock('@/lib/auth', () => ({ useCan: () => true }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const TEMPLATE = {
  id: 't1',
  contract_id: 'c1',
  nome: 'Fattura mensile',
  oggetto: 'Fattura {{numero_fattura}}',
  corpo: 'Gentile {{cliente}}',
  allegati: ['fattura_pdf', 'registro_pdf'],
  attivo: true,
  created_at: '2026-10-26T10:00:00Z',
  updated_at: '2026-10-26T10:00:00Z',
}
const INVOICE = {
  id: 'i1',
  anno: 2026,
  numero: 12,
  riferimento: null,
  totale: '1250.00',
  divisa: 'EUR',
}
const DRAFT = {
  id: 'd1',
  updated_at: '2026-10-26T11:00:00Z',
  to_addresses: ['mario@acme.example'],
  subject: 'Fattura 2026/12',
  body_markdown: 'Gentile Acme',
  attachment_version_ids: ['v1', 'v2'],
}
const PREVIEW = {
  subject: 'Fattura 2026/12',
  body: 'Gentile Acme S.r.l.,\n\nin allegato la fattura 2026/12.',
  to_suggeriti: ['mario@acme.example'],
  allegati: [
    { tipo: 'fattura_pdf', nome: 'Fattura 2026/12' },
    { tipo: 'registro_pdf', nome: 'Registro giornate 01/10/2026 - 31/10/2026' },
  ],
  da: '2026-10-01',
  a: '2026-10-31',
}

function mockGets(templates: unknown[]) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/invoices') return ok({ items: [INVOICE], next_cursor: null })
    if (path === '/api/email-drafts/{draft_id}') {
      return ok(DRAFT)
    }
    if (path.endsWith('/email-template-variables')) {
      return ok([{ nome: 'cliente', etichetta: 'Cliente' }])
    }
    return ok(templates)
  }) as never)
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ContractEmailSection contractId="c1" customerId="cust-1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  toast.success.mockReset()
})

describe('ContractEmailSection', () => {
  it('lists the saved templates with the files they carry', async () => {
    mockGets([TEMPLATE])
    renderSection()
    expect(await screen.findByText('Fattura mensile')).toBeInTheDocument()
    expect(screen.getByText(/allegati: fattura, registro/)).toBeInTheDocument()
  })

  it('offers no send until a template exists', async () => {
    mockGets([])
    renderSection()
    expect(await screen.findByText(/Nessun modello su questo contratto/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Invia fattura e registro' })).not.toBeInTheDocument()
  })

  it('saves a new template with the variables the server lists', async () => {
    mockGets([])
    vi.mocked(api.POST).mockImplementation((() => ok(TEMPLATE)) as never)
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo modello' }))
    await userEvent.type(screen.getByLabelText('Nome'), 'Fattura mensile')
    expect(screen.getByText(/\{\{cliente\}\}/, { selector: 'p' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/contracts/{contract_id}/email-templates',
        expect.objectContaining({
          body: expect.objectContaining({
            nome: 'Fattura mensile',
            allegati: ['fattura_pdf', 'registro_pdf'],
          }),
        }),
      ),
    )
  })

  it('sends the newest invoice with the first template in one press: draft, then send', async () => {
    mockGets([TEMPLATE])
    vi.mocked(api.POST).mockImplementation(((path: string) => {
      if (path.endsWith('/email/preview')) return ok(PREVIEW)
      if (path.endsWith('/email/draft')) return ok(DRAFT)
      return ok({ id: 'd1' })
    }) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia fattura e registro' }))
    // The subject and the attachment's name read alike: both are on screen.
    expect(await screen.findAllByText('Fattura 2026/12')).toHaveLength(2)
    expect(screen.getByLabelText('A')).toHaveValue('mario@acme.example')
    expect(screen.getByText('Registro giornate 01/10/2026 - 31/10/2026')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Invia' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/email-drafts/{draft_id}/send', {
        params: { path: { draft_id: 'd1' } },
        body: { updated_at: '2026-10-26T11:00:00Z' },
      }),
    )
    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/email/draft',
      expect.objectContaining({
        body: { template_id: 't1', invoice_id: 'i1', to_addresses: ['mario@acme.example'] },
      }),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  it('sends the same draft again after a failed send instead of making a second one', async () => {
    mockGets([TEMPLATE])
    let sends = 0
    vi.mocked(api.POST).mockImplementation(((path: string) => {
      if (path.endsWith('/email/preview')) return ok(PREVIEW)
      if (path.endsWith('/email/draft')) return ok(DRAFT)
      sends += 1
      return sends === 1
        ? (Promise.resolve({
            error: { status: 409, code: 'conflict', detail: 'Google non collegato' },
            response: new Response(null, { status: 409 }),
          }) as never)
        : ok({ id: 'd1' })
    }) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia fattura e registro' }))
    await screen.findAllByText('Fattura 2026/12')
    await userEvent.click(screen.getByRole('button', { name: 'Invia' }))
    await waitFor(() => expect(sends).toBe(1))
    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))

    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    const drafts = vi
      .mocked(api.POST)
      .mock.calls.filter((call) => String(call[0]).endsWith('/email/draft'))
    expect(drafts).toHaveLength(1)
  })

  it('does not send a draft that was edited after the person read it', async () => {
    mockGets([TEMPLATE])
    let reads = 0
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/invoices') return ok({ items: [INVOICE], next_cursor: null })
      if (path === '/api/email-drafts/{draft_id}') {
        reads += 1
        return ok({ ...DRAFT, subject: 'Un altro oggetto' })
      }
      if (path.endsWith('/email-template-variables')) return ok([])
      return ok([TEMPLATE])
    }) as never)
    vi.mocked(api.POST).mockImplementation(((path: string) =>
      path.endsWith('/email/preview') ? ok(PREVIEW) : ok(DRAFT)) as never)
    renderSection()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia fattura e registro' }))
    await screen.findAllByText('Fattura 2026/12')
    await userEvent.click(screen.getByRole('button', { name: 'Invia' }))

    expect(await screen.findByText(/è stata modificata dopo che l’hai vista/)).toBeInTheDocument()
    expect(reads).toBe(1)
    expect(vi.mocked(api.POST).mock.calls.some((c) => String(c[0]).endsWith('/send'))).toBe(false)
  })

  it('says when the customer has no issued invoice', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/invoices' ? ok({ items: [], next_cursor: null }) : ok([TEMPLATE])) as never)
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: 'Invia fattura e registro' }))
    expect(await screen.findByText(/non ha ancora una fattura emessa/)).toBeInTheDocument()
  })
})
