import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OrganizationDetail } from './OrganizationDetail'
import { api } from '@/lib/api'
import { spaceModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useParams: () => ({ organizationId: 'c1' }), useNavigate: () => vi.fn() }
})

// Hoisted so a single test can flip it: everything below is about what the page draws
// for a reader, and only the Fatture tab's button is about what a writer may do.
const auth = vi.hoisted(() => ({
  canWrite: false,
  admin: false,
  user: null as { id: string; name: string } | null,
}))
vi.mock('@/lib/auth', () => ({
  useCanWrite: () => auth.canWrite,
  // REB-294: the pages below mount controls that read their own action from the
  // table (`NewProformaButton`, `DocumentsTab`); they follow the same switch, so a
  // test asking for a writer gets every writer control and a reader gets none. The
  // owner's change (REB-773) is an admin's, so it follows its own switch.
  useCan: (action: string) => (action === 'change_customer_owner' ? auth.admin : auth.canWrite),
  useAuth: () => ({ user: auth.user }),
}))

// `api.GET` is spied on directly (not `vi.mock('@/lib/api', ...)`), mirroring
// Timeline.test.tsx: what is under test is this route's own handling of what the
// real `unwrap` produces (a resolved customer, a thrown `ProblemDetail`), not a
// reimplementation of either. `OrganizationDetail` also mounts `useEntitySchema`,
// `useOrganizationPeople` and `useOrganizationDeals` unconditionally, through the same
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

/** The notes thread is a bare array, like the timeline: handed a page envelope the
 *  overview's «Note» section would render `{items: []}.map` and take the tree down. */
const NOTES_PATH = '/api/{collection}/{entity_id}/notes'

function mockOrganizationFetch(result: ReturnType<typeof ok> | ReturnType<typeof failed>) {
  mockGet.mockImplementation(
    ((path: string) =>
      path === '/api/organizations/{organization_id}'
        ? result
        : path === NOTES_PATH
          ? ok([])
          : ok({ items: [] })) as never,
  )
}

beforeEach(() => {
  mockGet.mockReset()
  auth.canWrite = false
  auth.admin = false
  auth.user = null
})

/** Enough of a `OrganizationRead` for the page to render past its guards. Cast rather than
 *  typed in full: the fields this file asserts on are the tab strip, not the overview. */
const CUSTOMER = {
  id: 'c1',
  legal_name: 'ACME Srl',
  contact_points: [],
  custom_fields: {},
} as never

const POINTS = [
  { id: 'k1', kind: 'email', value: 'info@acme.it', label: 'work', note: null, is_primary: true, position: 0 },
  { id: 'k2', kind: 'email', value: 'fatture@acme.it', label: 'billing', note: 'solo fatture', is_primary: false, position: 1 },
  { id: 'k3', kind: 'phone', value: '+39 02 1234-567', label: 'work', note: 'centralino', is_primary: true, position: 2 },
  { id: 'k4', kind: 'linkedin', value: 'https://www.linkedin.com/company/acme', label: 'other', note: null, is_primary: true, position: 3 },
]

const NOTE = {
  id: 'n1',
  entity_type: 'organization',
  entity_id: 'c1',
  text: 'Richiamare a settembre.',
  author: 'Giulia Rossi',
  author_id: 'u1',
  source: 'web',
  created_at: '2026-10-01T09:00:00Z',
  edited_at: null,
  edited_by: null,
  editable: true,
} as never

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('OrganizationDetail', () => {
  /** REB-879: the Note card of the overview is the customer's thread, read from the
   *  notes endpoint and not from a field of the customer. */
  it('shows the customer’s notes thread in the overview', async () => {
    mockGet.mockImplementation(
      ((path: string) =>
        path === '/api/organizations/{organization_id}'
          ? ok(CUSTOMER)
          : path === NOTES_PATH
            ? ok([NOTE])
            : ok({ items: [] })) as never,
    )
    renderWithClient(<OrganizationDetail />)
    // The first render of the page in this file also pays the cold load of its lazy parts:
    // under a loaded machine that outlasts the default second, so this one waits for the
    // thread's own query, not for a guess about the clock.
    expect(await screen.findByText('Richiamare a settembre.', {}, { timeout: 10_000 })).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith(NOTES_PATH, {
      params: { path: { collection: 'organizations', entity_id: 'c1' } },
    })
  })

  /** REB-326: the terms the due date of every invoice to this customer is computed
   *  from, stated on the card so a wrong one is seen before an emission prints it. */
  describe('the payment terms row', () => {
    it('states the agreed terms, end of month included', async () => {
      mockOrganizationFetch(ok({ ...(CUSTOMER as object), payment_days: 30, payment_end_of_month: true }))
      renderWithClient(<OrganizationDetail />)
      expect(await screen.findByText('Termini di pagamento')).toBeInTheDocument()
      expect(screen.getByText('30 giorni data fattura fine mese')).toBeInTheDocument()
    })

    it('says the profile decides when the customer has no days of its own', async () => {
      mockOrganizationFetch(ok({ ...(CUSTOMER as object), payment_days: null, payment_end_of_month: false }))
      renderWithClient(<OrganizationDetail />)
      expect(await screen.findByText('Termini di pagamento')).toBeInTheDocument()
      expect(screen.getByText('Dal profilo fiscale')).toBeInTheDocument()
    })
  })

  /** REB-773: who answers for the customer, which is who its reminders go to. */
  describe('the owner row', () => {
    it('shows a dash for a customer nobody owns', async () => {
      mockOrganizationFetch(ok({ ...(CUSTOMER as object), owner_id: null }))
      renderWithClient(<OrganizationDetail />)
      const label = await screen.findByText('Responsabile')
      expect(label.parentElement).toHaveTextContent('Responsabile—')
    })

    it('names the signed-in person when the customer is theirs', async () => {
      auth.user = { id: 'u1', name: 'Giulia' }
      mockOrganizationFetch(ok({ ...(CUSTOMER as object), owner_id: 'u1' }))
      renderWithClient(<OrganizationDetail />)
      expect(await screen.findByText('Giulia (tu)')).toBeInTheDocument()
    })

    it('does not name a colleague to someone who cannot list the space', async () => {
      auth.user = { id: 'u1', name: 'Giulia' }
      mockOrganizationFetch(ok({ ...(CUSTOMER as object), owner_id: 'u2' }))
      renderWithClient(<OrganizationDetail />)
      expect(await screen.findByText('Un collega')).toBeInTheDocument()
    })

    it('names the owner to an admin, who can list the users', async () => {
      auth.admin = true
      auth.canWrite = true
      auth.user = { id: 'u1', name: 'Giulia' }
      mockGet.mockImplementation(((path: string) => {
        if (path === '/api/organizations/{organization_id}') {
          return ok({ ...(CUSTOMER as object), owner_id: 'u2' })
        }
        if (path === '/api/users') return ok([{ id: 'u2', name: 'Marco', active: true }])
        if (path === NOTES_PATH) return ok([])
        return ok({ items: [] })
      }) as never)
      renderWithClient(<OrganizationDetail />)
      expect(await screen.findByText('Marco')).toBeInTheDocument()
    })
  })

  /** REB-942: every way to reach the customer, each as a link of its kind. */
  describe('the contact points', () => {
    it('lists every point with its label, note and primary marker, in the order they are kept', async () => {
      mockOrganizationFetch(ok({ ...(CUSTOMER as object), contact_points: POINTS }))
      renderWithClient(<OrganizationDetail />)

      const main = await screen.findByRole('link', { name: 'info@acme.it' })
      expect(main).toHaveAttribute('href', 'mailto:info@acme.it')
      expect(screen.getByRole('link', { name: 'fatture@acme.it' })).toHaveAttribute(
        'href',
        'mailto:fatture@acme.it',
      )
      const phone = screen.getByRole('link', { name: '+39 02 1234-567' })
      expect(phone).toHaveAttribute('href', 'tel:+39021234567')

      const profile = screen.getByRole('link', { name: 'https://www.linkedin.com/company/acme' })
      expect(profile).toHaveAttribute('target', '_blank')
      expect(profile).toHaveAttribute('rel', 'noopener noreferrer')

      const rows = screen.getAllByTestId('contact-point')
      expect(rows).toHaveLength(4)
      expect(rows[1]).toHaveTextContent('amministrazione')
      expect(rows[1]).toHaveTextContent('solo fatture')
      expect(rows[1]).not.toHaveTextContent('Principale')
      expect(rows[0]).toHaveTextContent('Principale')
      expect(rows[2]).toHaveTextContent('centralino')
    })

    it('says so when the customer has none', async () => {
      mockOrganizationFetch(ok(CUSTOMER))
      renderWithClient(<OrganizationDetail />)
      expect(await screen.findByText('Nessun recapito.')).toBeInTheDocument()
    })
  })

  it('shows "Cliente non trovato" for a genuine 404', async () => {
    mockOrganizationFetch(
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
    renderWithClient(<OrganizationDetail />)
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
    mockOrganizationFetch(failed({ code: 'http_error', detail: 'Il server non risponde.', status: 503 }, 503))
    renderWithClient(<OrganizationDetail />)
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
          if (path === '/api/organizations/{organization_id}') return ok(CUSTOMER)
          if (path === '/api/gmail/account') return ok(health)
          if (path === NOTES_PATH) return ok([])
          return ok({ items: [] })
        }) as never,
      )
    }

    it('is offered when this installation has Gmail', async () => {
      mockPage({ account: null, banner: null, banner_text: null, missing_scopes: [], configured: true })
      renderWithClient(<OrganizationDetail />)
      expect(await screen.findByRole('tab', { name: 'Email' })).toBeInTheDocument()
    })

    it('is absent when Gmail is not configured on this installation', async () => {
      mockPage({ account: null, banner: null, banner_text: null, missing_scopes: [], configured: false })
      renderWithClient(<OrganizationDetail />)
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
    async function openInvoices() {
      mockGet.mockImplementation(
        ((path: string) =>
          path === '/api/organizations/{organization_id}'
            ? ok(CUSTOMER)
            : path === NOTES_PATH
              ? ok([])
              : ok({ items: [] })) as never,
      )
      renderWithClient(<OrganizationDetail />)
      await userEvent.click(await screen.findByRole('tab', { name: 'Fatture' }))
    }

    it('offers a new invoice to whoever may create one', async () => {
      auth.canWrite = true
      await openInvoices()
      expect(await screen.findByRole('button', { name: 'Nuova fattura' })).toBeInTheDocument()
    })

    it('offers none to a reader', async () => {
      await openInvoices()
      expect(await screen.findByText('Nessuna fattura.')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Nuova fattura' })).not.toBeInTheDocument()
    })
  })
})

/** REB-953: a core page that points into a switched-off module hides that part. */
describe('OrganizationDetail with modules off', () => {
  function mockWithModules(off: readonly ModuleKey[]) {
    mockGet.mockImplementation(
      ((path: string) =>
        path === '/api/settings/modules'
          ? ok(spaceModules(off))
          : path === '/api/organizations/{organization_id}'
            ? ok(CUSTOMER)
            : path === NOTES_PATH
              ? ok([])
              : ok({ items: [] })) as never,
    )
  }

  it('draws the Contratti tab and the Deal card while both modules are on', async () => {
    mockWithModules([])
    renderWithClient(<OrganizationDetail />)
    expect(await screen.findByRole('tab', { name: 'Contratti' }, { timeout: 10_000 })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Collegamenti' }))
    expect(await screen.findByText(/^Deal \(/)).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith('/api/deals', expect.anything())
  })

  it('draws no Deal card with deals off, and does not ask for the deals', async () => {
    mockWithModules(['deals'])
    renderWithClient(<OrganizationDetail />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Collegamenti' }, { timeout: 10_000 }))
    expect(await screen.findByText(/^Persone \(/)).toBeInTheDocument()
    expect(screen.queryByText(/^Deal \(/)).toBeNull()
    expect(mockGet).not.toHaveBeenCalledWith('/api/deals', expect.anything())
  })

  it('draws no Contratti tab with contracts off', async () => {
    mockWithModules(['contracts'])
    renderWithClient(<OrganizationDetail />)
    expect(await screen.findByRole('tab', { name: 'Fatture' }, { timeout: 10_000 })).toBeInTheDocument()
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('/api/settings/modules'))
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Contratti' })).toBeNull())
  })
})
