import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ContractDealLink } from './ContractDealLink'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    Link: ({ children, params }: { children: React.ReactNode; params: Record<string, string> }) => (
      <a href={`/deal/${params.dealId}`}>{children}</a>
    ),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

function renderLink(dealId: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ContractDealLink dealId={dealId} />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.mocked(api.GET).mockReset())

describe('ContractDealLink', () => {
  it('links a converted contract back to its deal, by the deal’s name', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
      Promise.resolve({
        data: { id: 'd1', name: 'Piattaforma' },
        response: new Response(null, { status: 200 }),
      })) as never)
    renderLink('d1')

    const link = await screen.findByRole('link', { name: 'Piattaforma' })
    expect(link).toHaveAttribute('href', '/deal/d1')
    expect(screen.getByText('Deal')).toBeInTheDocument()
  })

  it('shows nothing for a contract written by hand, and asks for no deal', () => {
    const { container } = renderLink(null)
    expect(container).toBeEmptyDOMElement()
    expect(api.GET).not.toHaveBeenCalled()
  })
})
