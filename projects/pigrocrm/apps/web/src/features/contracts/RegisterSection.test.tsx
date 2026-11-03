import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { downloadFromApi } from '@/lib/download'
import { RegisterSection } from './RegisterSection'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
const download = vi.hoisted(() => vi.fn())
vi.mock('@/features/documents/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/documents/queries')>()),
  downloadDocument: download,
}))
vi.mock('@/lib/download', () => ({ downloadFromApi: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/lib/auth', () => ({ useCan: () => true }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const REGISTER = {
  contract_id: 'c1',
  title: 'Consulenza',
  customer: 'ACME Srl',
  from: '2026-10-01',
  to: '2026-10-31',
  entries: [
    {
      work_unit_id: 'w1',
      date: '2026-10-07',
      quantity: '1.00',
      description: 'Giornata',
      status: 'invoiced',
      invoice_number: '2026/12',
      approval: {
        channel: 'email',
        sender: 'mario@cliente.example',
        received_at: '2026-10-02T09:30:00',
        message_id: '<a@b>',
      },
    },
    {
      work_unit_id: 'w2',
      date: '2026-10-08',
      quantity: '0.50',
      description: 'Mezza giornata',
      status: 'invoiced',
      invoice_number: null,
      approval: null,
    },
  ],
  quantity_total: '1.50',
  without_approval: 1,
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RegisterSection contractId="c1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 9, 26))
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  download.mockReset()
})

describe('RegisterSection', () => {
  it('shows the month the server lists, with its total and the days without approval', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(REGISTER)) as never)
    renderSection()

    expect(await screen.findByText('Giornata')).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/register',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' }, query: { from: '2026-10-01', to: '2026-10-31' } },
      }),
    )
    expect(screen.getAllByText('Fatturato')).toHaveLength(2)
    expect(screen.getByText('2026/12')).toBeInTheDocument()
    expect(screen.getByText('Bozza')).toBeInTheDocument()
    expect(screen.getByText('email · mario@cliente.example')).toBeInTheDocument()
    expect(screen.getByText('Totale: 1,5')).toBeInTheDocument()
    expect(screen.getByText(/1 giornata è nel registro senza approvazione/)).toBeInTheDocument()
  })

  it('downloads the CSV of the period and downloads the PDF through its archived document', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(REGISTER)) as never)
    vi.mocked(api.POST).mockImplementation((() => ok({ id: 'doc-1' })) as never)
    renderSection()
    await screen.findByText('Giornata')

    await userEvent.click(screen.getByRole('button', { name: 'CSV' }))
    expect(downloadFromApi).toHaveBeenCalledWith(
      '/api/contracts/c1/register/csv?from=2026-10-01&to=2026-10-31',
    )
    await userEvent.click(screen.getByRole('button', { name: 'PDF' }))

    expect(api.POST).toHaveBeenCalledWith(
      '/api/contracts/{contract_id}/register/pdf',
      expect.objectContaining({
        params: { path: { contract_id: 'c1' }, query: { from: '2026-10-01', to: '2026-10-31' } },
      }),
    )
    await waitFor(() => expect(download).toHaveBeenCalledWith('doc-1'))
  })

  it('says so when the month has no billed day', async () => {
    vi.mocked(api.GET).mockImplementation(
      (() => ok({ ...REGISTER, entries: [], quantity_total: '0', without_approval: 0 })) as never,
    )
    renderSection()
    expect(await screen.findByText('Nessuna giornata fatturata in questo mese.')).toBeInTheDocument()
  })
})
