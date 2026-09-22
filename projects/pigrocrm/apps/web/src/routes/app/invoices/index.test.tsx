/**
 * The invoice list's own header and filter row (design spec §4).
 *
 * The page is what the revision changed: the title and the primary action moved into
 * `PageHeader`, and the «stato» filter -- a `Select` until now -- became the row of
 * squared chips the application-variant record draws. The chips are the part worth a
 * test: they are the only filter on this screen a user drives, and a chip that looks
 * pressed while the list behind it is unfiltered is a page lying about what it shows.
 *
 * `api.GET` is spied on directly rather than `useInvoices` being mocked, mirroring
 * `customers/$customerId.test.tsx`: what is asserted is the query the page actually
 * sends.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement, ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { InvoicesList } from './index'

// REB-294: the list's «Nuova fattura» is the button that used to answer a readonly
// press with a 403; it now asks `useCan('create_invoice')`. The suite renders as the
// writer it has always asserted for, and the readonly shape is its own test below.
const mockAuth = vi.hoisted(() => ({ may: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => mockAuth.may }))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    // A real `Link` needs a router context this component test has no reason to stand
    // up; what matters here is that the page renders one, not what TanStack does with
    // it.
    Link: ({ children }: { children: ReactNode }) => <a href="#">{children}</a>,
  }
})

const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

beforeEach(() => {
  mockAuth.may = true
  mockGet.mockReset()
  mockGet.mockImplementation((() => ok({ items: [], next_cursor: null })) as never)
})

function renderList(scadute?: boolean): ReactElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <InvoicesList scadute={scadute} />
    </QueryClientProvider>,
  )
  return <></>
}

/** One issued invoice as the API sends it, with whatever a test needs changed. */
function invoice(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'f1',
    anno: 2026,
    numero: 7,
    riferimento: null,
    tipo: 'fattura',
    stato: 'emessa',
    stato_pagamento: 'da_incassare',
    data_emissione: '2026-08-20',
    competenza_da: null,
    competenza_a: null,
    totale: '1500.00',
    customer_ragione_sociale: 'ACME S.r.l.',
    ...overrides,
  }
}

/** Makes the list endpoint answer these rows; every other endpoint stays empty. */
function mockInvoices(items: Record<string, unknown>[]): void {
  mockGet.mockImplementation(((path: string) =>
    path === '/api/invoices'
      ? ok({ items, next_cursor: null })
      : ok({ items: [], next_cursor: null })) as never)
}

/** Makes the list endpoint answer two pages: the first carries `next_cursor`, and a
 *  request that sends that cursor back gets the second page with none after it. */
function mockInvoicesPages(
  first: Record<string, unknown>[],
  second: Record<string, unknown>[],
): void {
  mockGet.mockImplementation(((
    path: string,
    options?: { params?: { query?: Record<string, unknown> } },
  ) => {
    if (path !== '/api/invoices') return ok({ items: [], next_cursor: null })
    const cursor = options?.params?.query?.cursor
    return cursor === undefined
      ? ok({ items: first, next_cursor: 'page-2' })
      : ok({ items: second, next_cursor: null })
  }) as never)
}

/** The query the page last sent to the list endpoint. */
function lastRequestedQuery(): Record<string, unknown> {
  const calls = mockGet.mock.calls.filter((call) => call[0] === '/api/invoices')
  const last = calls[calls.length - 1]?.[1] as
    | { params?: { query?: Record<string, unknown> } }
    | undefined
  return last?.params?.query ?? {}
}

/** The `stato` the page last asked the API for, or `undefined` when it asked for none. */
function lastRequestedStato(): unknown {
  return lastRequestedQuery().stato
}

describe('the invoice list', () => {
  it('opens with its title as the page heading', async () => {
    renderList()
    expect(await screen.findByRole('heading', { level: 1, name: 'Fatture' })).toBeInTheDocument()
  })

  it('offers its primary action in the header', async () => {
    renderList()
    expect(await screen.findByRole('button', { name: /nuova fattura/i })).toBeInTheDocument()
  })

  it('shows one state chip per invoice state, «Tutte» pressed to begin with', async () => {
    renderList()
    const filters = await screen.findByRole('search')
    expect(within(filters).getByRole('button', { name: 'Tutte' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    for (const label of ['Bozza', 'Emessa', 'Annullata', 'Confermata', 'Consumata']) {
      expect(within(filters).getByRole('button', { name: label })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
    }
  })

  it('asks the API for the state whose chip is pressed', async () => {
    renderList()
    const filters = await screen.findByRole('search')
    expect(lastRequestedStato()).toBeUndefined()

    await userEvent.click(within(filters).getByRole('button', { name: 'Emessa' }))
    expect(within(filters).getByRole('button', { name: 'Emessa' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(lastRequestedStato()).toBe('emessa')
  })

  it('clears the state filter when the pressed chip is pressed again', async () => {
    renderList()
    const filters = await screen.findByRole('search')
    await userEvent.click(within(filters).getByRole('button', { name: 'Emessa' }))
    await userEvent.click(within(filters).getByRole('button', { name: 'Emessa' }))

    expect(lastRequestedStato()).toBeUndefined()
    expect(within(filters).getByRole('button', { name: 'Tutte' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  /**
   * Under «Tutte» the list showed 2026/18 and the proforma it came from as two rows with
   * the same customer, period and total (ORB-169). The page now asks the server to leave
   * the consumed ones out whenever no state is chosen; the «Consumata» chip asks for
   * exactly them, so nothing is unreachable.
   */
  it('leaves consumed proformas out under «Tutte» and lists them under «Consumata»', async () => {
    renderList()
    const filters = await screen.findByRole('search')
    expect(lastRequestedQuery()).toMatchObject({ escludi_consumate: true })
    expect(lastRequestedStato()).toBeUndefined()

    await userEvent.click(within(filters).getByRole('button', { name: 'Consumata' }))
    expect(lastRequestedStato()).toBe('consumata')
    expect(lastRequestedQuery().escludi_consumate).toBeUndefined()

    await userEvent.click(within(filters).getByRole('button', { name: 'Emessa' }))
    expect(lastRequestedQuery().escludi_consumate).toBeUndefined()

    await userEvent.click(within(filters).getByRole('button', { name: 'Emessa' }))
    expect(lastRequestedQuery()).toMatchObject({ escludi_consumate: true })
  })

  it('keeps the type filter as a select in the same row', async () => {
    renderList()
    const filters = await screen.findByRole('search')
    expect(within(filters).getByLabelText('Filtra per tipo')).toBeInTheDocument()
  })

  /** The list is the one screen that mixes customers, so it is the one that says whose
   *  invoice each row is (ORB-98). The tab inside a customer's page does not: see
   *  `InvoicesTab.test.tsx`. */
  it('says which customer each invoice belongs to', async () => {
    mockInvoices([invoice()])
    renderList()
    expect(await screen.findByRole('columnheader', { name: 'Cliente' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'ACME S.r.l.' })).toBeInTheDocument()
  })

  it('says what each invoice is for, right after the customer (ORB-130)', async () => {
    mockInvoices([invoice({ causale: 'Consulenza agosto', importata_da: 'esterno' })])
    renderList()
    const headers = (await screen.findAllByRole('columnheader')).map((h) => h.textContent)
    expect(headers.indexOf('Descrizione')).toBe(headers.indexOf('Cliente') + 1)
    expect(screen.getByRole('cell', { name: 'Consulenza agosto' })).toBeInTheDocument()
    expect(screen.queryByText(/^importata$/i)).toBeNull()
  })

  it('says which period each invoice is about, beside its date (ORB-126)', async () => {
    mockInvoices([
      invoice({
        data_emissione: '2026-09-02',
        competenza_da: '2026-08-01',
        competenza_a: '2026-08-31',
      }),
    ])
    renderList()
    const headers = (await screen.findAllByRole('columnheader')).map((h) => h.textContent)
    expect(headers.indexOf('Competenza')).toBe(headers.indexOf('Data') + 1)
    expect(screen.getByRole('cell', { name: '01/08/2026 - 31/08/2026' })).toBeInTheDocument()
  })

  it('still explains the «scadute» drill-through it arrives with', async () => {
    renderList(true)
    expect(await screen.findByRole('status')).toHaveTextContent(/scadute e non incassate/i)
  })

  /**
   * `useInvoices` sends no `limit`/`cursor` and the page dropped `next_cursor` on the
   * floor (REB-231): a space with more than one page of invoices had everything past
   * the first fifty silently unreachable. The button walks the cursor one page at a
   * time, the way `fetchAllDeals` already does for the Kanban.
   */
  it('offers "Carica altre" when the page is truncated, and loads the next page on click', async () => {
    mockInvoicesPages(
      [
        invoice({ id: 'f1', customer_ragione_sociale: 'ACME S.r.l.' }),
        invoice({ id: 'f1b', customer_ragione_sociale: 'ACME S.r.l.' }),
      ],
      [invoice({ id: 'f2', customer_ragione_sociale: 'Beta S.r.l.' })],
    )
    renderList()

    expect((await screen.findAllByRole('cell', { name: 'ACME S.r.l.' }))).toHaveLength(2)
    expect(screen.queryByRole('cell', { name: 'Beta S.r.l.' })).not.toBeInTheDocument()

    const loadMore = screen.getByRole('button', { name: 'Carica altre' })
    expect(screen.getByText('Mostrate 2 fatture, ce ne sono altre.')).toBeInTheDocument()

    await userEvent.click(loadMore)

    expect(await screen.findByRole('cell', { name: 'Beta S.r.l.' })).toBeInTheDocument()
    expect(screen.getAllByRole('cell', { name: 'ACME S.r.l.' })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Carica altre' })).not.toBeInTheDocument()
    // The second request has to carry the exact cursor the first page returned, not
    // merely *some* cursor: a stale or wrong-but-defined value would still pass every
    // assertion above.
    expect(lastRequestedQuery().cursor).toBe('page-2')
  })

  /** REB-294, the card's own symptom: the Fatture page showed «Nuova fattura» to a
   *  readonly person, and the press answered 403. The list itself -- what a role may
   *  read -- is unchanged. */
  it('offers a readonly person no «Nuova fattura»', async () => {
    mockAuth.may = false
    mockGet.mockImplementation((() => ok({ items: [invoice()], next_cursor: null })) as never)
    renderList()
    await screen.findByRole('columnheader', { name: 'Numero' })
    expect(screen.queryByRole('button', { name: /nuova fattura/i })).not.toBeInTheDocument()
  })
})
