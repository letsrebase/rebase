import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ok, refused, renderWithQuery, viewport } from '@/features/inbox/testing'
import { EngagementsPage } from './EngagementsPage'
import { engagement } from './listFixtures'
import type { EngagementsSearch } from './search'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() } }
})

const navigate = vi.fn()
let search: EngagementsSearch = { tab: 'todo' }
let admin = true

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    params,
    children,
    ...rest
  }: {
    to: string
    params?: Record<string, string>
    children: React.ReactNode
    className?: string
  }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to} {...rest}>
      {children}
    </a>
  ),
  useNavigate: () => navigate,
  useSearch: () => search,
}))

vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => admin,
  useCanWrite: () => admin,
  useCan: () => admin,
  useAuth: () => ({ user: { id: 'u1', role: admin ? 'admin' : 'readonly' }, isLoading: false }),
}))

const COUNTS = { todo: 3, ongoing: 7, closed: 41 }
const EMPTY_PAGE = { items: [], next_cursor: null }

/** Answers each path; `pages` is what `/api/engagements` says, in order, the last one repeated. */
function answering(pages: unknown[], counts: unknown = ok(COUNTS)) {
  let call = 0
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/engagements') return Promise.resolve(pages[Math.min(call++, pages.length - 1)])
    if (path === '/api/engagements/counts') return Promise.resolve(counts)
    return Promise.resolve(ok(EMPTY_PAGE))
  }) as never)
}

const page = (items: unknown[], nextCursor: string | null = null) => ok({ items, next_cursor: nextCursor })

function listCalls() {
  const calls = vi.mocked(api.GET).mock.calls as unknown as [
    string,
    { params: { query: Record<string, unknown> } },
  ][]
  return calls.filter(([path]) => path === '/api/engagements')
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  navigate.mockReset()
  search = { tab: 'todo' }
  admin = true
  viewport(1280)
})

describe('«Collaboratori» on a desk', () => {
  it('draws the header with its sentence, one strong button, the rows and the counts on the tabs', async () => {
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Collaboratori' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Le lettere di incarico dei freelance: dalla bozza alla firma, i giorni e i pagamenti.',
      ),
    ).toBeInTheDocument()
    expect(await screen.findByText('Giulia Martini')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Nuovo incarico' })).toHaveLength(1)
    expect(await screen.findByRole('tab', { name: /^Da fare\s*3$/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /^In corso\s*7$/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /^Chiusi\s*41$/ })).toBeInTheDocument()
  })

  it('asks for the tab’s bucket, 50 at a time, with the filters of the URL', async () => {
    const freelancer = '5b2c70c2-0000-4000-8000-000000000001'
    search = { tab: 'closed', freelancer, state: 'concluded' }
    answering([page([engagement({ state: 'concluded' })])])
    renderWithQuery(<EngagementsPage />)
    await screen.findByText('Giulia Martini')
    expect(listCalls()[0]?.[1].params.query).toMatchObject({
      bucket: 'closed',
      freelancer_id: freelancer,
      state: 'concluded',
      limit: 50,
    })
  })

  it('draws no count until it arrived, and none when the read of the counts fails', async () => {
    answering([page([engagement()])], refused(500, { title: 'Errore', detail: 'No' }))
    renderWithQuery(<EngagementsPage />)
    await screen.findByText('Giulia Martini')
    expect(screen.getByRole('tab', { name: 'Da fare' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'In corso' })).toBeInTheDocument()
  })

  it('puts the tab in the URL when another tab is pressed', async () => {
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: /In corso/ }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/engagements',
      search: { tab: 'ongoing', freelancer: undefined, client: undefined, state: undefined },
      replace: true,
    })
  })

  it('puts the state filter in the URL', async () => {
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    await userEvent.click(await screen.findByRole('combobox', { name: 'Stato' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Attivo' }))
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ search: expect.objectContaining({ tab: 'todo', state: 'active' }) }),
    )
  })

  it('says «Mostra altri (N in tutto)» for the tab, and follows the cursor', async () => {
    answering([page([engagement()], 'c2'), page([engagement({ id: 'e2', freelancer_name: 'Luca Bianchi' })])])
    renderWithQuery(<EngagementsPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Mostra altri (3 in tutto)' }))
    expect(await screen.findByText('Luca Bianchi')).toBeInTheDocument()
    expect(listCalls()[1]?.[1].params.query).toMatchObject({ cursor: 'c2' })
    expect(screen.queryByRole('button', { name: /Mostra altri/ })).not.toBeInTheDocument()
  })

  it('leaves the total out of «Mostra altri» when a filter is set', async () => {
    search = { tab: 'todo', state: 'draft' }
    answering([page([engagement()], 'c2')])
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByRole('button', { name: 'Mostra altri' })).toBeInTheDocument()
  })

  it('says «Niente da fare: nessun incarico aspetta te.» on an empty «Da fare»', async () => {
    answering([page([])])
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByText('Niente da fare: nessun incarico aspetta te.')).toBeInTheDocument()
  })

  it('says «Nessun incarico.» with its sentence and «Nuovo incarico» on an empty list', async () => {
    search = { tab: 'ongoing' }
    answering([page([])])
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByText('Nessun incarico.')).toBeInTheDocument()
    expect(
      screen.getByText('Qui vedi le lettere di incarico dei freelance, dalla bozza alla firma.'),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Nuovo incarico' })).toHaveLength(2)
  })

  it('says the list is empty with these filters, not that there are no engagements', async () => {
    search = { tab: 'ongoing', state: 'active' }
    answering([page([])])
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByText('Nessun incarico con questi filtri.')).toBeInTheDocument()
  })

  it('waits with «Leggo gli incarichi…»', async () => {
    vi.mocked(api.GET).mockImplementation((() => new Promise(() => {})) as never)
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByRole('status', { name: 'Leggo gli incarichi…' })).toBeInTheDocument()
  })

  it('shows the server’s error with «Riprova» and nothing under it, no counts included', async () => {
    answering([refused(500, { title: 'Errore', detail: 'Il server non risponde.' })])
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByText('Il server non risponde.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Da fare' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /Da fare\s*\d/ })).not.toBeInTheDocument()
  })

  it('opens the new-engagement dialog from «Nuovo incarico»', async () => {
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo incarico' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Nuovo incarico' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Crea la bozza' })).toBeInTheDocument()
  })

  it('offers the freelancer and the client filters', async () => {
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    const filters = await screen.findByRole('search', { name: 'Filtra l’elenco' })
    expect(within(filters).getByRole('button', { name: 'Freelance' })).toBeInTheDocument()
    expect(within(filters).getByRole('button', { name: 'Cliente' })).toBeInTheDocument()
  })
})

describe('«Collaboratori» for anyone who is not an administrator', () => {
  it('answers one sentence, no figure and no request', async () => {
    admin = false
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    expect(
      await screen.findByText('Gli incarichi dei freelance li vedono solo gli amministratori.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nuovo incarico' })).not.toBeInTheDocument()
    expect(screen.queryByText(/€/)).not.toBeInTheDocument()
    expect(listCalls()).toHaveLength(0)
    const paths = (vi.mocked(api.GET).mock.calls as unknown as [string][]).map(([path]) => path)
    expect(paths).not.toContain('/api/engagements/counts')
  })
})

describe('«Collaboratori» on a phone', () => {
  beforeEach(() => viewport(390))

  it('draws cards, a sticky «Nuovo incarico» and a «Filtri» button, with no table', async () => {
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    expect(await screen.findByRole('list', { name: 'Incarichi' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Nuovo incarico' })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Filtri' })).toBeInTheDocument()
  })
})

describe('the form the hub’s «Crea match» opens', () => {
  const HUB_ID = '0197f0b2-7a3c-7b1e-9c53-2f0a6a1d4e55'
  const handoff = {
    tab: 'todo',
    hub_match_id: HUB_ID,
    freelancer_id: '0197f0b2-0000-7000-8000-000000000001',
    deal_id: '0197f0b2-0000-7000-8000-000000000002',
    client_id: '0197f0b2-0000-7000-8000-000000000003',
  } satisfies EngagementsSearch

  it('asks for the engagement under that id first, and opens the form filled in when there is none', async () => {
    search = handoff
    answering([page([])])
    renderWithQuery(<EngagementsPage />)

    expect(await screen.findByRole('heading', { name: 'Nuovo incarico' })).toBeInTheDocument()
    expect(listCalls().some(([, init]) => init.params.query.hub_match_id === HUB_ID)).toBe(true)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('shows the engagement that already has the id instead of a second form', async () => {
    search = handoff
    answering([page([engagement({ id: 'e7' })])])
    renderWithQuery(<EngagementsPage />)

    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/engagements/$engagementId',
        params: { engagementId: 'e7' },
        replace: true,
      }),
    )
    expect(screen.queryByRole('heading', { name: 'Nuovo incarico' })).not.toBeInTheDocument()
  })

  it('opens no form for a plain visit', async () => {
    answering([page([engagement()])])
    renderWithQuery(<EngagementsPage />)
    await screen.findByRole('button', { name: /Nuovo incarico/ })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
