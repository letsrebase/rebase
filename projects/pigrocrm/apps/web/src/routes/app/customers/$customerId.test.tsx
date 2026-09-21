import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CustomerDetail } from './$customerId'
import { api } from '@/lib/api'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useParams: () => ({ customerId: 'c1' }), useNavigate: () => vi.fn() }
})

// Hoisted so a single test can flip it: everything below is about what the page draws
// for a reader, and only the Fatture tab's button is about what a writer may do.
const auth = vi.hoisted(() => ({ canWrite: false }))
vi.mock('@/lib/auth', () => ({ useCanWrite: () => auth.canWrite }))

// `api.GET` is spied on directly (not `vi.mock('@/lib/api', ...)`), mirroring
// Timeline.test.tsx: what is under test is this route's own handling of what the
// real `unwrap` produces (a resolved customer, a thrown `ProblemDetail`), not a
// reimplementation of either. `CustomerDetail` also mounts `useEntitySchema`,
// `useCustomerPeople` and `useCustomerDeals` unconditionally, through the same
// client -- every path other than the customer fetch itself below resolves to a
// harmless empty success, since none of it is ever read before the isError/404
// guard under test here returns.
const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

function mockCustomerFetch(result: ReturnType<typeof ok> | ReturnType<typeof failed>) {
  mockGet.mockImplementation(
    ((path: string) => (path === '/api/customers/{customer_id}' ? result : ok({ items: [] }))) as never,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  auth.canWrite = false
})

/** Enough of a `CustomerRead` for the page to render past its guards. Cast rather than
 *  typed in full: the fields this file asserts on are the tab strip, not the overview. */
const CUSTOMER = {
  id: 'c1',
  ragione_sociale: 'ACME Srl',
  custom_fields: {},
} as never

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('CustomerDetail', () => {
  it('shows "Cliente non trovato" for a genuine 404', async () => {
    mockCustomerFetch(
      failed(
        {
          type: 'about:blank',
          title: 'Non trovato',
          status: 404,
          detail: 'cliente non trovato',
          code: 'not_found',
        },
        404,
      ),
    )
    renderWithClient(<CustomerDetail />)
    expect(await screen.findByText('Cliente non trovato.')).toBeInTheDocument()
  })

  /**
   * The bug this guards against: before this fix, `if (!customer) return <p>
   * Cliente non trovato.</p>` fired for *any* failed fetch, not only a real
   * 404 -- a 500, a 502 or a dropped connection told the user the record does
   * not exist. This reuses `QueryErrorBanner`, the same surface `DataTable` and
   * `Timeline` already show for a failed request everywhere else in this app,
   * rather than inventing a fourth way to say "something went wrong".
   */
  it('shows the failed-request banner, not "Cliente non trovato", when the fetch fails for a reason other than 404', async () => {
    mockCustomerFetch(failed({ code: 'http_error', detail: 'Il server non risponde.', status: 503 }, 503))
    renderWithClient(<CustomerDetail />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByText('Cliente non trovato.')).not.toBeInTheDocument()
  })

  /**
   * The Email tab exists only where Gmail could ever fill it. An installation with no
   * Google client answers `configured: false`, and a tab there would open onto a
   * permanent "nessuna email" -- the same reason `EntityDetailLayout` renders no
   * Economia tab rather than a tab full of zeros.
   */
  describe('the Email tab', () => {
    function mockPage(health: unknown) {
      mockGet.mockImplementation(
        ((path: string) => {
          if (path === '/api/customers/{customer_id}') return ok(CUSTOMER)
          if (path === '/api/gmail/account') return ok(health)
          return ok({ items: [] })
        }) as never,
      )
    }

    it('is offered when this installation has Gmail', async () => {
      mockPage({ account: null, banner: null, banner_text: null, missing_scopes: [], configured: true })
      renderWithClient(<CustomerDetail />)
      expect(await screen.findByRole('tab', { name: 'Email' })).toBeInTheDocument()
    })

    it('is absent when Gmail is not configured on this installation', async () => {
      mockPage({ account: null, banner: null, banner_text: null, missing_scopes: [], configured: false })
      renderWithClient(<CustomerDetail />)
      // Awaited on a tab that is always present, so this is an assertion about the
      // rendered page rather than about a page that had not rendered yet.
      expect(await screen.findByRole('tab', { name: 'Panoramica' })).toBeInTheDocument()
      expect(screen.queryByRole('tab', { name: 'Email' })).not.toBeInTheDocument()
    })
  })

  /**
   * A fattura is nearly always for a customer one is already looking at, so the tab that
   * lists them is also where a new one starts -- with the customer already answered
   * (`NewProformaButton customerId=`), rather than a picker the reader has to fill in
   * with the record they came from.
   */
  describe('the Fatture tab', () => {
    async function openFatture() {
      mockGet.mockImplementation(
        ((path: string) =>
          path === '/api/customers/{customer_id}' ? ok(CUSTOMER) : ok({ items: [] })) as never,
      )
      renderWithClient(<CustomerDetail />)
      await userEvent.click(await screen.findByRole('tab', { name: 'Fatture' }))
    }

    it('offers a new invoice to whoever may create one', async () => {
      auth.canWrite = true
      await openFatture()
      expect(await screen.findByRole('button', { name: 'Nuova fattura' })).toBeInTheDocument()
    })

    it('offers none to a reader', async () => {
      await openFatture()
      expect(await screen.findByText('Nessuna fattura.')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Nuova fattura' })).not.toBeInTheDocument()
    })
  })
})
