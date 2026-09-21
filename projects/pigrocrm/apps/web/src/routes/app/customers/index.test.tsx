/**
 * The customer list's header and filter row (design spec §4): the title and «Nuovo
 * cliente» come from `PageHeader`, and the search box lives in the filter row above the
 * table rather than floating between a local `<header>` and the rows.
 *
 * `api.GET` is spied on directly, mirroring `customers/$customerId.test.tsx`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { CustomersPage } from './index'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => vi.fn() }
})

vi.mock('@/lib/auth', () => ({ useCanWrite: () => true }))

const mockGet = vi.spyOn(api, 'GET')

beforeEach(() => {
  mockGet.mockReset()
  mockGet.mockImplementation((() =>
    Promise.resolve({
      data: { items: [], next_cursor: null, custom_fields: [] },
      response: new Response(null, { status: 200 }),
    })) as never)
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <CustomersPage initialSearch="" />
    </QueryClientProvider>,
  )
}

describe('the customer list', () => {
  it('opens with its title as the page heading', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Clienti' })).toBeInTheDocument()
  })

  it('offers its primary action in the header', async () => {
    renderPage()
    expect(await screen.findByRole('button', { name: /nuovo cliente/i })).toBeInTheDocument()
  })

  it('puts the search box in the filter row', async () => {
    renderPage()
    const filters = await screen.findByRole('search')
    expect(within(filters).getByPlaceholderText(/cerca per ragione sociale/i)).toBeInTheDocument()
  })
})
