/**
 * The customer list's header and filter row (design spec §4): the title and «Nuovo
 * cliente» come from `PageHeader`, and the search box lives in the filter row above the
 * table rather than floating between a local `<header>` and the rows.
 *
 * `api.GET` is spied on directly, mirroring `customers/$customerId.test.tsx`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { CustomersPage } from './index'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => vi.fn() }
})

vi.mock('@/lib/auth', () => ({ useCanWrite: () => true }))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const mockGet = vi.spyOn(api, 'GET')
const mockPost = vi.spyOn(api, 'POST')

const PAGE = { items: [], next_cursor: null, custom_fields: [] }
const NO_MAILBOX = { account: null, banner: null, banner_text: null, missing_scopes: [], configured: true }
const MAILBOX = { ...NO_MAILBOX, account: { email_address: 'ada@studio.it', status: 'active' } }
const PROPOSALS = [
  { dominio: 'acme.it', nome: 'Acme', conversazioni: 2, ultimo_messaggio: null, persone: [] },
]

function answering(gmail: unknown) {
  mockGet.mockImplementation(((path: string) =>
    Promise.resolve({
      data:
        path === '/api/gmail/account'
          ? gmail
          : path === '/api/gmail/customer-suggestions'
            ? PROPOSALS
            : PAGE,
      response: new Response(null, { status: 200 }),
    })) as never)
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
  answering(NO_MAILBOX)
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

describe('«Proponi dalla casella» (REB-223)', () => {
  it('is offered only once a mailbox is connected', async () => {
    renderPage()
    await screen.findByRole('button', { name: /nuovo cliente/i })
    expect(screen.queryByRole('button', { name: 'Proponi dalla casella' })).toBeNull()
  })

  it('opens the proposals, reads Gmail only then, and closes after an import', async () => {
    answering(MAILBOX)
    mockPost.mockImplementation((() =>
      Promise.resolve({ data: [{ id: 'c1' }], response: new Response(null, { status: 201 }) })) as never)
    const user = userEvent.setup()
    renderPage()

    const open = await screen.findByRole('button', { name: 'Proponi dalla casella' })
    expect(mockGet).not.toHaveBeenCalledWith('/api/gmail/customer-suggestions')
    await user.click(open)
    const dialog = await screen.findByRole('dialog', { name: 'Proponi dalla casella' })
    await user.click(await within(dialog).findByRole('checkbox', { name: /Acme/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Importa 1 cliente' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(mockPost).toHaveBeenCalledWith('/api/customers/from-suggestions', expect.anything())
  })
})
