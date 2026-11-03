import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { InvoicesTab } from './InvoicesTab'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => vi.fn() }
})

const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

beforeEach(() => {
  mockGet.mockReset()
  mockGet.mockImplementation((() => ok({ items: [], next_cursor: null })) as never)
})

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('InvoicesTab', () => {
  it('renders nothing above the table when the page offers no action', async () => {
    renderWithClient(<InvoicesTab owner={{ customerId: 'c1' }} />)
    expect(await screen.findByText('Nessuna fattura.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  /** A consumed proforma's number lives on the invoice it became; the tab shows the
   *  invoice and not both, like the list page under «Tutte» (ORB-169). */
  it('asks the server to leave out consumed proformas', async () => {
    renderWithClient(<InvoicesTab owner={{ dealId: 'd1' }} />)
    await screen.findByText('Nessuna fattura.')
    const call = mockGet.mock.calls.find((c) => c[0] === '/api/invoices')
    const options = call?.[1] as { params?: { query?: Record<string, unknown> } } | undefined
    expect(options?.params?.query).toEqual({ deal_id: 'd1', exclude_consumed: true })
  })

  /** Every row on this tab belongs to the customer (or the deal's customer) named in
   *  the page title above it, so the «Cliente» column the list page shows (ORB-98) would
   *  only repeat that title on each line. */
  it('does not repeat the customer on every row', async () => {
    renderWithClient(<InvoicesTab owner={{ customerId: 'c1' }} />)
    expect(await screen.findByRole('columnheader', { name: 'Numero' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Cliente' })).not.toBeInTheDocument()
  })

  /** The period is not what the page title already says, so the tab shows it (ORB-126). */
  it('says which period each invoice is about, like the list page', async () => {
    renderWithClient(<InvoicesTab owner={{ customerId: 'c1' }} />)
    expect(await screen.findByRole('columnheader', { name: 'Competenza' })).toBeInTheDocument()
  })

  /** The tab has no customer column, so the description follows the number (ORB-130). */
  it('says what each invoice is for, right after the number', async () => {
    renderWithClient(<InvoicesTab owner={{ customerId: 'c1' }} />)
    const headers = (await screen.findAllByRole('columnheader')).map((h) => h.textContent)
    expect(headers.indexOf('Descrizione')).toBe(headers.indexOf('Numero') + 1)
  })

  /**
   * The slot exists so the button that creates an invoice sits on the tab that lists
   * them, rather than in the page header where it would be offered from every other tab
   * too. The tab knows nothing about what goes in it: the customer page passes a
   * `NewProformaButton` bound to itself, and a deal page could pass something else.
   */
  it('renders the action the page gave it, above the table', async () => {
    renderWithClient(
      <InvoicesTab owner={{ customerId: 'c1' }} actions={<button>Nuova fattura</button>} />,
    )
    expect(await screen.findByText('Nessuna fattura.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nuova fattura' })).toBeInTheDocument()
  })
})
