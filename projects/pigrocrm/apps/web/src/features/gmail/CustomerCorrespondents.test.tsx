import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from '@rebase/ui/sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CustomerCorrespondents } from './CustomerCorrespondents'
import { api } from '@/lib/api'
import type { Role } from '@/lib/permissions'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const mockAuth = vi.hoisted(() => ({ role: 'admin' as Role }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return { useCan: (action: string) => can(mockAuth.role, action) }
})

const CUSTOMER_ID = '00000000-0000-7000-8000-000000000001'
const ASK = 'Cerca chi ha scritto dal dominio del cliente'

function ok(data: unknown, status = 200) {
  return { data, response: new Response(null, { status }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

function report(corrispondenti: unknown[] = found()) {
  return ok({
    started_at: '2026-10-20T09:00:00Z',
    dominio: 'acme.it',
    threads_scanned: 3,
    messages_seen: 4,
    corrispondenti,
  })
}

function found() {
  return [
    {
      indirizzo: 'marco@acme.it',
      nome: 'Marco Bianchi',
      messaggi: 3,
      ultimo_messaggio: '2026-10-18T10:00:00Z',
      gia_in_anagrafica: false,
    },
    {
      indirizzo: 'sara@acme.it',
      nome: '',
      messaggi: 1,
      ultimo_messaggio: '2026-09-01T10:00:00Z',
      gia_in_anagrafica: true,
    },
  ]
}

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CustomerCorrespondents customerId={CUSTOMER_ID} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockAuth.role = 'admin'
})

describe('CustomerCorrespondents', () => {
  it('asks Gmail nothing until the person presses', () => {
    renderIt()
    expect(screen.getByRole('button', { name: ASK })).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalled()
  })

  it('lists the addresses with their messages and last one, and marks those already filed', async () => {
    vi.mocked(api.GET).mockResolvedValue(report())
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: ASK }))

    const list = await screen.findByRole('list', { name: 'Indirizzi di acme.it nella casella' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Marco Bianchi')
    expect(rows[0]).toHaveTextContent('marco@acme.it')
    expect(rows[0]).toHaveTextContent('3 messaggi')
    expect(rows[0]).toHaveTextContent('l’ultimo il')
    expect(rows[1]).toHaveTextContent('1 messaggio')
    expect(rows[1]).toHaveTextContent('già in anagrafica')
    expect(within(rows[1]!).queryByRole('button')).toBeNull()
    expect(api.GET).toHaveBeenCalledWith('/api/gmail/customers/{customer_id}/correspondents', {
      params: { path: { customer_id: CUSTOMER_ID } },
    })
  })

  it('adds one address as a person of the customer and marks it filed', async () => {
    vi.mocked(api.GET).mockResolvedValue(report())
    vi.mocked(api.POST).mockResolvedValue(ok({ id: 'p1' }, 201))
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: ASK }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Aggiungi come persona: Marco Bianchi' }),
    )

    expect(api.POST).toHaveBeenCalledWith('/api/customers/{customer_id}/people-from-mail', {
      params: { path: { customer_id: CUSTOMER_ID } },
      body: { indirizzo: 'marco@acme.it', nome: 'Marco Bianchi' },
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Marco Bianchi aggiunto alle persone'))
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Aggiungi come persona: Marco Bianchi' }),
      ).toBeNull(),
    )
    // Not read again: that would ask Gmail about the whole domain a second time.
    expect(api.GET).toHaveBeenCalledTimes(1)
  })

  it('says so when the customer has no domain to start from', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      failed({ detail: 'il cliente non ha un dominio da cui partire', code: 'conflict' }, 409),
    )
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: ASK }))
    expect(await screen.findByRole('alert')).toHaveTextContent('non ha un dominio')
  })

  it('stops offering an address the book got in the meantime, and says why', async () => {
    vi.mocked(api.GET).mockResolvedValue(report())
    vi.mocked(api.POST).mockResolvedValue(
      failed({ detail: 'customer: marco@acme.it è già in anagrafica', code: 'conflict' }, 409),
    )
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: ASK }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Aggiungi come persona: Marco Bianchi' }),
    )

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('già in anagrafica')))
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Aggiungi come persona: Marco Bianchi' }),
      ).toBeNull(),
    )
  })

  it('offers a retry after a failed read', async () => {
    vi.mocked(api.GET)
      .mockResolvedValueOnce(
        failed({ detail: 'Gmail non ha risposto correttamente. Riprova più tardi', code: 'conflict' }, 409),
      )
      .mockResolvedValueOnce(report())
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: ASK }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Riprova più tardi')
    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(await screen.findByRole('list', { name: 'Indirizzi di acme.it nella casella' })).toBeInTheDocument()
  })

  it('says so when nobody at the domain wrote', async () => {
    vi.mocked(api.GET).mockResolvedValue(report([]))
    renderIt()
    await userEvent.click(screen.getByRole('button', { name: ASK }))
    expect(await screen.findByText(/Nessuno ha scritto/)).toBeInTheDocument()
  })

  it('offers nothing to a readonly person', () => {
    mockAuth.role = 'readonly'
    const { container } = renderIt()
    expect(container).toBeEmptyDOMElement()
  })
})
