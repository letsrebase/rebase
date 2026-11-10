/**
 * «Anagrafiche», the registry page (the approved brief of 2026-11-10): the title is the same on
 * every view and the view is a tab (`?role=`), the one strong button, the columns and the
 * empty, loading and error states of each view, the phone's cards, the module-off sentence and
 * what a read-only member does not get.
 *
 * `api.GET` is spied on directly, mirroring `organizations/$organizationId.test.tsx`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { ModuleKey } from '@/lib/modules'
import { seedModules } from '@/lib/modules.testing'
import { OrganizationsPage } from './OrganizationsPage'
import type { Organization } from './queries'
import type { RegistryRole } from './search'

const nav = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => nav.navigate }
})

const auth = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/lib/auth', () => ({ useCanWrite: () => auth.canWrite }))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const mockGet = vi.spyOn(api, 'GET')
const mockPost = vi.spyOn(api, 'POST')

const NO_MAILBOX = { account: null, banner: null, banner_text: null, missing_scopes: [], configured: true }
const MAILBOX = { ...NO_MAILBOX, account: { email_address: 'ada@studio.it', status: 'active' } }
const PROPOSALS = [
  { domain: 'acme.it', name: 'Acme', conversations: 2, last_message: null, people: [] },
]

function organization(overrides: Partial<Organization>): Organization {
  return {
    id: 'o1',
    legal_entity_id: null,
    owner_id: null,
    legal_name: 'ACME Srl',
    vat_number: null,
    tax_code: null,
    sdi_code: null,
    certified_email: null,
    address: null,
    postal_code: null,
    city: null,
    province: null,
    country: 'IT',
    email: null,
    phone: null,
    contact_points: [],
    website: null,
    status: null,
    payment_days: null,
    payment_end_of_month: false,
    supplier_payment_days: null,
    supplier_payment_end_of_month: false,
    roles: ['customer'],
    supplier_kind: null,
    supplier_area: null,
    supplier_supplies: null,
    customer_kind: null,
    is_withholding_agent: false,
    hub_freelancer_id: null,
    custom_fields: {},
    created_at: '2026-08-06T00:00:00Z',
    updated_at: '2026-08-06T00:00:00Z',
    ...overrides,
  }
}

const LONG_NAME = 'Consorzio Cooperative Artigiane del Nord Est Italia e Servizi S.r.l.'

const CUSTOMERS = [
  organization({ id: 'c1', legal_name: 'Rossi Costruzioni', vat_number: '01234567890', city: 'Milano', email: 'info@rossi.it' }),
  organization({ id: 'c2', legal_name: LONG_NAME, roles: ['customer'], phone: '+39 02 1234567' }),
]
const SUPPLIERS = [
  organization({
    id: 's1',
    legal_name: 'Cloudstore Inc.',
    roles: ['supplier'],
    country: 'US',
    vat_number: 'US123456789',
    supplier_kind: 'foreign',
    supplier_area: 'extra_eu',
    supplier_supplies: 'services',
    supplier_payment_days: 30,
    supplier_payment_end_of_month: true,
    email: 'billing@cloudstore.example',
  }),
  organization({
    id: 's2',
    legal_name: 'Zanetti Impianti S.r.l.',
    roles: ['customer', 'supplier'],
    vat_number: '09876543210',
    supplier_kind: 'company',
    supplier_payment_days: 60,
  }),
]

type Answers = {
  gmail?: unknown
  /** What `/api/organizations` answers per `role`; a function to hold or fail it. */
  organizations?: (role: string, search: string | undefined) => unknown
  /** `total` per role for the tab counts; the number of rows the answer holds when left out. */
  totals?: Partial<Record<string, number>>
}

function answering({ gmail = NO_MAILBOX, organizations, totals }: Answers = {}) {
  mockGet.mockImplementation(((path: string, init?: { params?: { query?: Record<string, string> } }) => {
    const respond = (data: unknown) =>
      Promise.resolve({ data, response: new Response(null, { status: 200 }) })
    if (path === '/api/gmail/account') return respond(gmail)
    if (path === '/api/gmail/organization-suggestions') return respond(PROPOSALS)
    if (path === '/api/organizations') {
      const query = init?.params?.query ?? {}
      const role = query.role ?? 'customer'
      const answer = organizations
        ? organizations(role, query.search)
        : {
            items: role === 'supplier' ? SUPPLIERS : role === 'any' ? [...CUSTOMERS, ...SUPPLIERS] : CUSTOMERS,
            next_cursor: null,
            total: totals?.[role] ?? (role === 'supplier' ? 2 : role === 'any' ? 4 : 2),
          }
      return answer instanceof Promise ? answer : respond(answer)
    }
    return respond({ items: [], next_cursor: null, custom_fields: [] })
  }) as never)
}

/** `window.matchMedia` for a viewport `width` wide: every `(min-width: Npx)` query it is asked. */
function viewport(width: number) {
  window.matchMedia = ((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query)
    return {
      matches: min === null ? false : width >= Number(min[1]),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }
  }) as never
}

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
  nav.navigate.mockReset()
  auth.canWrite = true
  viewport(1280)
  answering()
})

function renderPage(role: RegistryRole = 'customer', off: readonly ModuleKey[] = [], initialSearch = '') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  seedModules(client, off)
  render(
    <QueryClientProvider client={client}>
      <OrganizationsPage initialSearch={initialSearch} role={role} />
    </QueryClientProvider>,
  )
}

type Query = { role?: string; limit?: number }
function organizationRequests() {
  return (mockGet.mock.calls as unknown as unknown[][]).filter(([path]) => path === '/api/organizations')
}
const limitOf = ([, init]: unknown[]) => (init as { params: { query: Query } }).params.query.limit
/** The reads of the rows, and apart from them the one-row reads that count a role for its tab. */
function listRequests() {
  return organizationRequests().filter((call) => limitOf(call) !== 1)
}
function countRequests() {
  return organizationRequests().filter((call) => limitOf(call) === 1)
}

describe('the page', () => {
  it.each(['customer', 'supplier', 'any'] as const)(
    'is «Anagrafiche» with its one sentence on the %s view, the view being a tab and not a second title',
    async (role) => {
      renderPage(role)
      expect(await screen.findByRole('heading', { level: 1, name: 'Anagrafiche' })).toBeInTheDocument()
      expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
      expect(
        screen.getByText('Le aziende con cui lavori: clienti, fornitori o entrambi.'),
      ).toBeInTheDocument()
    },
  )

  it('draws the three views as tabs, the active one following `role`', async () => {
    renderPage('supplier')
    const tabs = within(await screen.findByRole('tablist'))
    expect(tabs.getAllByRole('tab').map((tab) => tab.textContent?.replace(/\d+$/, ''))).toEqual([
      'Tutte',
      'Clienti',
      'Fornitori',
    ])
    expect(tabs.getByRole('tab', { name: /^Fornitori/ })).toHaveAttribute('aria-selected', 'true')
    expect(tabs.getByRole('tab', { name: /^Clienti/ })).toHaveAttribute('aria-selected', 'false')
  })

  it('draws the count of each role beside its tab, from one one-row request per role', async () => {
    answering({ totals: { any: 212, customer: 180, supplier: 32 } })
    renderPage('customer')
    expect(await screen.findByRole('tab', { name: /^Tutte\s*212$/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /^Clienti\s*180$/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /^Fornitori\s*32$/ })).toBeInTheDocument()
    expect(countRequests().map(([, init]) => (init as { params: { query: Query } }).params.query.role).sort()).toEqual([
      'any',
      'customer',
      'supplier',
    ])
  })

  it('draws no count that never arrived, and never a 0 in its place', async () => {
    answering({
      organizations: (role) =>
        role === 'supplier'
          ? Promise.resolve({ error: { code: 'x', detail: 'no', status: 500, title: 'x', type: 'x' }, response: new Response(null, { status: 500 }) })
          : { items: CUSTOMERS, next_cursor: null, total: 2 },
    })
    renderPage('customer')
    expect(await screen.findByRole('tab', { name: /^Clienti\s*2$/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Fornitori' })).toBeInTheDocument()
  })

  it('does not count suppliers with the purchases module off', async () => {
    renderPage('customer', ['purchases'])
    await screen.findByRole('tab', { name: /^Clienti\s*2$/ })
    expect(organizationRequests().some(([, init]) => (init as { params: { query: Query } }).params.query.role === 'supplier')).toBe(false)
  })

  it('changes the view through the URL, «Clienti» being the page with no role', async () => {
    const user = userEvent.setup()
    renderPage('customer')
    await user.click(await screen.findByRole('tab', { name: /^Fornitori/ }))
    expect(nav.navigate).toHaveBeenLastCalledWith({ to: '/app/organizations', search: { role: 'supplier' } })
    await user.click(screen.getByRole('tab', { name: /^Tutte/ }))
    expect(nav.navigate).toHaveBeenLastCalledWith({ to: '/app/organizations', search: { role: 'any' } })
  })

  it('goes back to «Clienti» with no role in the URL', async () => {
    const user = userEvent.setup()
    renderPage('supplier')
    await user.click(await screen.findByRole('tab', { name: /^Clienti/ }))
    expect(nav.navigate).toHaveBeenLastCalledWith({ to: '/app/organizations', search: { role: undefined } })
  })

  it('asks the server for the role of the view, each view keeping its own list', async () => {
    renderPage('supplier')
    await screen.findByText('Cloudstore Inc.')
    expect(listRequests()[0]![1]).toMatchObject({ params: { query: { role: 'supplier' } } })
  })

  it('puts the search box and, on the supplier view only, «Tipo fiscale» in the filter row', async () => {
    renderPage('supplier')
    const filters = await screen.findByRole('search')
    expect(within(filters).getByPlaceholderText(/cerca per ragione sociale/i)).toBeInTheDocument()
    expect(within(filters).getByRole('combobox', { name: 'Tipo fiscale' })).toBeInTheDocument()
  })

  it('has no «Tipo fiscale» outside the supplier view', async () => {
    renderPage('any')
    const filters = await screen.findByRole('search')
    expect(within(filters).queryByRole('combobox')).toBeNull()
  })

  it('filters suppliers by fiscal kind through the API, «Tutti i tipi» sending none', async () => {
    const user = userEvent.setup()
    renderPage('supplier')
    const select = await screen.findByRole('combobox', { name: 'Tipo fiscale' })
    expect(select).toHaveTextContent('Tutti i tipi')
    expect(listRequests().at(-1)![1]).not.toMatchObject({ params: { query: { supplier_kind: expect.anything() } } })
    await user.click(select)
    await user.click(await screen.findByRole('option', { name: 'Professionista' }))
    await waitFor(() =>
      expect(listRequests().at(-1)![1]).toMatchObject({
        params: { query: { role: 'supplier', supplier_kind: 'professional' } },
      }),
    )
    await user.click(screen.getByRole('combobox', { name: 'Tipo fiscale' }))
    await user.click(await screen.findByRole('option', { name: 'Tutti i tipi' }))
    await waitFor(() =>
      expect(listRequests().at(-1)![1]).not.toMatchObject({ params: { query: { supplier_kind: 'professional' } } }),
    )
  })

  it('sends the typed term with the role once it settles', async () => {
    const user = userEvent.setup()
    renderPage('any')
    await user.type(await screen.findByPlaceholderText(/cerca per ragione sociale/i), 'zanetti')
    await waitFor(() =>
      expect(listRequests().at(-1)![1]).toMatchObject({
        params: { query: { role: 'any', search: 'zanetti' } },
      }),
    )
  })
})

describe('the strong button follows the view', () => {
  it.each([
    ['customer', 'Nuovo cliente'],
    ['supplier', 'Nuovo fornitore'],
    ['any', 'Nuova anagrafica'],
  ] as const)('on %s it reads «%s» and is the only one', async (role, label) => {
    renderPage(role)
    expect(await screen.findByRole('button', { name: label })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Nuov[oa] / })).toHaveLength(1)
  })

  it('opens the supplier view\'s form as a supplier', async () => {
    const user = userEvent.setup()
    renderPage('supplier')
    await user.click(await screen.findByRole('button', { name: 'Nuovo fornitore' }))
    const dialog = await screen.findByRole('dialog', { name: 'Nuovo fornitore' })
    expect(within(dialog).getByRole('checkbox', { name: 'Fornitore' })).toBeChecked()
    expect(within(dialog).getByRole('heading', { name: 'Come fornitore' })).toBeInTheDocument()
  })

  it('creates through the form with the roles of the view', async () => {
    const user = userEvent.setup()
    mockPost.mockResolvedValue({ data: organization({}), response: new Response(null, { status: 201 }) } as never)
    renderPage('supplier')
    await user.click(await screen.findByRole('button', { name: 'Nuovo fornitore' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Ragione sociale/), 'Nuovo Fornitore Srl')
    await user.click(within(dialog).getByRole('button', { name: 'Salva' }))
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/organizations', {
        body: expect.objectContaining({ legal_name: 'Nuovo Fornitore Srl', roles: ['supplier'] }),
      }),
    )
  })

  it('is not offered to a read-only member, who still lists and reads', async () => {
    auth.canWrite = false
    renderPage('customer')
    expect(await screen.findByText('Rossi Costruzioni')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Nuov[oa] / })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Proponi dalla casella' })).toBeNull()
  })
})

describe('the columns of each view', () => {
  it('Clienti keeps today\'s', async () => {
    renderPage('customer')
    await screen.findByText('Rossi Costruzioni')
    const headers = screen.getAllByRole('columnheader').map((header) => header.textContent)
    expect(headers).toEqual(['Ragione sociale', 'P.IVA', 'Comune', 'Email', 'Telefono'])
  })

  it('Fornitori has the fiscal kind and the terms, and no «Da pagare»', async () => {
    renderPage('supplier')
    await screen.findByText('Cloudstore Inc.')
    const headers = screen.getAllByRole('columnheader').map((header) => header.textContent)
    expect(headers).toEqual(['Ragione sociale', 'P.IVA', 'Tipo fiscale', 'Pagamento'])
    expect(screen.queryByText('Da pagare')).toBeNull()
    // A foreign supplier reads where it is and what it supplies; the terms are short.
    expect(screen.getByText('Estero · extra UE · servizi')).toBeInTheDocument()
    expect(screen.getByText('30 gg f.m.')).toBeInTheDocument()
    expect(screen.getByText('Società')).toBeInTheDocument()
    expect(screen.getByText('60 gg')).toBeInTheDocument()
  })

  it('Tutte carries the roles as outlined chips beside the name', async () => {
    renderPage('any')
    await screen.findByText('Zanetti Impianti S.r.l.')
    const headers = screen.getAllByRole('columnheader').map((header) => header.textContent)
    expect(headers).toEqual(['Ragione sociale', 'P.IVA', 'Comune', 'Email'])
    const row = screen.getByText('Zanetti Impianti S.r.l.').closest('tr')!
    const chips = [...row.querySelectorAll('[data-slot="badge"]')]
    expect(chips.map((chip) => chip.textContent)).toEqual(['Cliente', 'Fornitore'])
    for (const chip of chips) expect(chip).toHaveAttribute('data-variant', 'outline')
    expect(row.querySelector('[data-tone]')).toBeNull()
  })

  it('truncates a 60-character name in the cell and keeps the whole of it as the title', async () => {
    renderPage('customer')
    const name = await screen.findByText(LONG_NAME)
    expect(name).toHaveClass('truncate')
    expect(name.closest('[title]')).toHaveAttribute('title', LONG_NAME)
  })

  it('opens an organization from its row', async () => {
    const user = userEvent.setup()
    renderPage('supplier')
    await user.click(await screen.findByText('Cloudstore Inc.'))
    expect(nav.navigate).toHaveBeenCalledWith({
      to: '/app/organizations/$organizationId',
      params: { organizationId: 's1' },
    })
  })
})

describe('the phone', () => {
  beforeEach(() => viewport(390))

  it('draws one column of cards, not the table, and a card opens the organization', async () => {
    const user = userEvent.setup()
    renderPage('any')
    const card = await screen.findByRole('button', { name: /Zanetti Impianti S\.r\.l\./ })
    expect(screen.queryByRole('table')).toBeNull()
    // Name, the roles, the fiscal kind and terms of a supplier.
    expect(within(card).getByText('Cliente')).toBeInTheDocument()
    expect(within(card).getByText('Fornitore')).toBeInTheDocument()
    expect(within(card).getByText('Società · 60 gg')).toBeInTheDocument()
    await user.click(card)
    expect(nav.navigate).toHaveBeenCalledWith({
      to: '/app/organizations/$organizationId',
      params: { organizationId: 's2' },
    })
  })

  it('shows the contact on the card, and the whole name, which wraps', async () => {
    renderPage('customer')
    const card = await screen.findByRole('button', { name: new RegExp(LONG_NAME.slice(0, 20)) })
    expect(within(card).getByText(LONG_NAME)).toHaveClass('break-words')
    expect(within(card).getByText('+39 02 1234567')).toBeInTheDocument()
    expect(within(await screen.findByRole('button', { name: /Rossi Costruzioni/ })).getByText('info@rossi.it')).toBeInTheDocument()
  })

  it('says «Leggo le anagrafiche…» while waiting, and shows the error with «Riprova»', async () => {
    mockGet.mockImplementation((() => new Promise(() => {})) as never)
    renderPage('customer')
    expect(await screen.findByRole('status', { name: 'Leggo le anagrafiche…' })).toBeInTheDocument()
  })
})

describe('the states', () => {
  it.each([
    ['customer', 'Nessun cliente. Creane uno per iniziare.', 'Nuovo cliente'],
    ['supplier', 'Nessun fornitore. Compaiono quando registri una fattura passiva o ne crei uno.', 'Nuovo fornitore'],
    ['any', 'Nessuna anagrafica. Creane una per iniziare.', 'Nuova anagrafica'],
  ] as const)('is empty on %s with its sentence and the one action', async (role, sentence, action) => {
    answering({ organizations: () => ({ items: [], next_cursor: null }) })
    renderPage(role)
    expect(await screen.findByText(sentence)).toBeInTheDocument()
    // The header's button and the one under the sentence are the same action, twice.
    expect(screen.getAllByRole('button', { name: action }).length).toBeGreaterThanOrEqual(1)
  })

  it('offers a read-only member the sentence and no action', async () => {
    auth.canWrite = false
    answering({ organizations: () => ({ items: [], next_cursor: null }) })
    renderPage('supplier')
    expect(await screen.findByText(/Nessun fornitore\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nuovo fornitore' })).toBeNull()
  })

  it('says a search found nothing, and does not tell the person to create one', async () => {
    answering({ organizations: (_role, search) => ({ items: search ? [] : CUSTOMERS, next_cursor: null }) })
    const user = userEvent.setup()
    renderPage('customer')
    await user.type(await screen.findByPlaceholderText(/cerca per ragione sociale/i), 'zzz')
    expect(await screen.findByText('Nessun risultato per «zzz».')).toBeInTheDocument()
  })

  it('shows «Leggo le anagrafiche…» in the place of the rows while the list is on its way', async () => {
    mockGet.mockImplementation(((path: string) =>
      path === '/api/organizations'
        ? new Promise(() => {})
        : Promise.resolve({ data: NO_MAILBOX, response: new Response(null, { status: 200 }) })) as never)
    renderPage('customer')
    expect(await screen.findByRole('status', { name: 'Leggo le anagrafiche…' })).toBeInTheDocument()
    expect(screen.queryByText(/^Nessun /)).toBeNull()
  })

  it('shows the error with «Riprova» and no list under it, and asks again on «Riprova»', async () => {
    const user = userEvent.setup()
    let fail = true
    answering({
      organizations: () =>
        fail
          ? Promise.resolve({
              error: { code: 'unknown', detail: 'Il server non risponde.', status: 500, title: 'x', type: 'x' },
              response: new Response(null, { status: 500 }),
            })
          : { items: CUSTOMERS, next_cursor: null },
    })
    renderPage('customer')
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByText(/^Nessun /)).toBeNull()

    fail = false
    await user.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(await screen.findByText('Rossi Costruzioni')).toBeInTheDocument()
  })

  it('says how many there are in all in «Carica altri» when nothing narrows the list', async () => {
    mockGet.mockImplementation(((path: string) =>
      Promise.resolve({
        data: path === '/api/organizations' ? { items: CUSTOMERS, next_cursor: 'next', total: 212 } : NO_MAILBOX,
        response: new Response(null, { status: 200 }),
      })) as never)
    renderPage('customer')
    expect(await screen.findByText('Mostrati 2 clienti su 212.')).toBeInTheDocument()
  })

  it('pages with «Carica altri»', async () => {
    const user = userEvent.setup()
    mockGet.mockImplementation(((path: string, init?: { params?: { query?: { cursor?: string } } }) => {
      const respond = (data: unknown) =>
        Promise.resolve({ data, response: new Response(null, { status: 200 }) })
      if (path !== '/api/organizations') return respond(NO_MAILBOX)
      return init?.params?.query?.cursor
        ? respond({ items: [SUPPLIERS[0]], next_cursor: null })
        : respond({ items: CUSTOMERS, next_cursor: 'next' })
    }) as never)
    renderPage('customer')
    expect(await screen.findByText('Mostrati 2 clienti, ce ne sono altri.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Carica altri' }))
    expect(await screen.findByText('Cloudstore Inc.')).toBeInTheDocument()
  })
})

describe('with the purchases module off', () => {
  it('draws no «Fornitori» tab', async () => {
    renderPage('customer', ['purchases'])
    await screen.findByText('Rossi Costruzioni')
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent?.replace(/\d+$/, ''))).toEqual(['Tutte', 'Clienti'])
  })

  it('answers a stale link to the supplier view with the sentence, and reads no list', async () => {
    renderPage('supplier', ['purchases'])
    expect(
      await screen.findByText('Il modulo Acquisti è spento: un amministratore può accenderlo in Impostazioni → Moduli.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Anagrafiche' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nuovo fornitore' })).toBeNull()
    expect(listRequests()).toHaveLength(0)
  })

  it('draws no supplier role in the form of a new organization', async () => {
    const user = userEvent.setup()
    renderPage('any', ['purchases'])
    await user.click(await screen.findByRole('button', { name: 'Nuova anagrafica' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('checkbox', { name: 'Fornitore' })).toBeNull()
    expect(within(dialog).queryByRole('heading', { name: 'Come fornitore' })).toBeNull()
  })
})

describe('«Proponi dalla casella» (REB-223)', () => {
  it('is offered only once a mailbox is connected', async () => {
    renderPage('customer')
    await screen.findByRole('button', { name: 'Nuovo cliente' })
    expect(screen.queryByRole('button', { name: 'Proponi dalla casella' })).toBeNull()
  })

  it('belongs to the customer view', async () => {
    answering({ gmail: MAILBOX })
    renderPage('supplier')
    await screen.findByRole('button', { name: 'Nuovo fornitore' })
    expect(screen.queryByRole('button', { name: 'Proponi dalla casella' })).toBeNull()
  })

  it('opens the proposals, reads Gmail only then, and closes after an import', async () => {
    answering({ gmail: MAILBOX })
    mockPost.mockImplementation((() =>
      Promise.resolve({ data: [{ id: 'c1' }], response: new Response(null, { status: 201 }) })) as never)
    const user = userEvent.setup()
    renderPage('customer')

    const open = await screen.findByRole('button', { name: 'Proponi dalla casella' })
    expect(mockGet).not.toHaveBeenCalledWith('/api/gmail/organization-suggestions')
    await user.click(open)
    const dialog = await screen.findByRole('dialog', { name: 'Proponi dalla casella' })
    await user.click(await within(dialog).findByRole('checkbox', { name: /Acme/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Importa 1 cliente' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(mockPost).toHaveBeenCalledWith('/api/organizations/from-suggestions', expect.anything())
  })
})
