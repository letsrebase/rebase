import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatDate } from '@/features/invoices/format'
import { api } from '@/lib/api'
import { RenewalChain } from './RenewalChain'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    Link: ({ children, params }: { children: React.ReactNode; params: { contractId: string } }) => (
      <a href={`/app/contracts/${params.contractId}`}>{children}</a>
    ),
  }
})

const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const NEIGHBOURS: Record<string, unknown> = {
  c0: { id: 'c0', titolo: 'Sviluppo 2025', inizio: '2025-01-01', fine: '2025-12-31' },
  c2: { id: 'c2', titolo: 'Sviluppo 2027', inizio: '2027-01-01', fine: null },
}

function renderChain(precedente: string | null, successivo: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RenewalChain
        contract={{ contratto_precedente_id: precedente, contratto_successivo_id: successivo }}
      />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  mockGet.mockImplementation(((_path: string, init: { params: { path: { contract_id: string } } }) =>
    ok(NEIGHBOURS[init.params.path.contract_id])) as never)
})

describe('RenewalChain', () => {
  it('links both ends with the neighbours’ titles and dates', async () => {
    renderChain('c0', 'c2')

    expect(screen.getByText('Rinnovato da')).toBeInTheDocument()
    expect(screen.getByText('Rinnovato in')).toBeInTheDocument()
    const previous = await screen.findByRole('link', { name: 'Sviluppo 2025' })
    expect(previous).toHaveAttribute('href', '/app/contracts/c0')
    expect(
      await screen.findByText(`${formatDate('2025-01-01')} - ${formatDate('2025-12-31')}`),
    ).toBeInTheDocument()
    const next = await screen.findByRole('link', { name: 'Sviluppo 2027' })
    expect(next).toHaveAttribute('href', '/app/contracts/c2')
    expect(await screen.findByText(`dal ${formatDate('2027-01-01')}`)).toBeInTheDocument()
  })

  it('names the links plainly until the neighbour has loaded, and when it never does', async () => {
    mockGet.mockImplementation((() => failed({ status: 404, code: 'not_found', detail: 'x' }, 404)) as never)
    renderChain('c0', 'c2')

    expect(screen.getByRole('link', { name: 'Contratto precedente' })).toHaveAttribute(
      'href',
      '/app/contracts/c0',
    )
    expect(screen.getByRole('link', { name: 'Contratto successivo' })).toHaveAttribute(
      'href',
      '/app/contracts/c2',
    )
    await vi.waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('link', { name: 'Contratto precedente' })).toBeInTheDocument()
  })

  it('shows only the end that exists', async () => {
    renderChain(null, 'c2')

    expect(screen.queryByText('Rinnovato da')).not.toBeInTheDocument()
    expect(screen.getByText('Rinnovato in')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Sviluppo 2027' })).toBeInTheDocument()
  })

  it('shows nothing for a contract that was never renewed and is not a renewal', () => {
    const { container } = renderChain(null, null)

    expect(container).toBeEmptyDOMElement()
    expect(mockGet).not.toHaveBeenCalled()
  })
})
