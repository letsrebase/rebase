import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { SETTINGS_GROUPS } from '@/features/settings/tabs'
import { readAndClearRegisterHandoffEmail } from '@/lib/registerHandoff'
import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'
import { AppShell } from './AppShell'

const mockRoute = vi.hoisted(() => ({ pathname: '/app/customers', search: '' }))

/**
 * `Link` is substituted rather than mounted in a router, as everywhere else in this
 * codebase -- but the substitute now has to honour `activeProps`, because that is how the
 * sidebar marks the current page (`aria-current="page"`) and a mock that dropped it would
 * make the assertion below pass or fail for the wrong reason.
 */
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    className,
    activeProps,
    activeOptions,
    'aria-current': ariaCurrent,
    'data-status': dataStatus,
  }: {
    children: React.ReactNode
    to: string
    className?: string
    activeProps?: Record<string, unknown>
    activeOptions?: { exact?: boolean; includeSearch?: boolean }
    'aria-current'?: 'page'
    'data-status'?: string
  }) => {
    // `activeOptions.exact` is honoured, because the shell relies on it: without it "/app"
    // is a prefix of every other route and Home would be the current page everywhere.
    const path = activeOptions?.exact
      ? mockRoute.pathname === to
      : mockRoute.pathname === to || mockRoute.pathname.startsWith(`${to}/`)
    // So is `includeSearch`, which TanStack defaults to *true*: a link that carries no
    // search of its own then stops being active the moment the URL carries any, and
    // every sidebar entry here is such a link.
    const search = activeOptions?.includeSearch === false || mockRoute.search === ''
    const active = path && search
    return (
      <a
        href={to}
        className={className}
        aria-current={ariaCurrent}
        data-status={dataStatus}
        {...(active ? activeProps : {})}
      >
        {children}
      </a>
    )
  },
  useRouterState: () => ({ location: { pathname: mockRoute.pathname } }),
  // The command palette the shell mounts navigates; nothing here asserts on where.
  useNavigate: () => vi.fn(),
}))

// The shell toasts a refusal to enter another space, which is what the switcher's tests read.
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const mockAuth = vi.hoisted(() => ({ role: 'admin' as string, id: undefined as string | undefined }))
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: mockAuth.id, nome: 'Mario', email: 'm@example.com', role: mockAuth.role }, logout: vi.fn() }),
  useIsAdmin: () => mockAuth.role === 'admin',
}))

vi.mock('@/lib/tenant', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/tenant')>()
  // Pinned to a real slug so the switcher's exclusion of "the space already open"
  // (AppShell.tsx) is actually exercised, rather than every test running as if it
  // were the unprefixed root.
  return { ...actual, tenantPrefix: '/studio' }
})

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

import { api } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'

const GET = api.GET as unknown as Mock
const POST = api.POST as unknown as Mock
const mockGo = vi.fn()

/**
 * The shell mounts the command palette, which is a TanStack Query consumer, so the
 * provider is part of the harness rather than of any one test. Its query is disabled
 * below three characters, so nothing here issues a request.
 */
function renderShell(children: React.ReactNode = <div />, legal_entity?: LegalEntityValue) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // A fresh element on every call: re-rendering the *same* element object is a React
  // bail-out, which would make `refresh()` (the stand-in for a navigation) do nothing.
  // Without `legal_entity` the shell runs on the context's default, a one-legal-entity space.
  const tree = () => (
    <QueryClientProvider client={client}>
      {legal_entity ? (
        <LegalEntityContext value={legal_entity}>
          <AppShell go={mockGo}>{children}</AppShell>
        </LegalEntityContext>
      ) : (
        <AppShell go={mockGo}>{children}</AppShell>
      )}
    </QueryClientProvider>
  )
  const result = render(tree())
  return { ...result, refresh: () => result.rerender(tree()) }
}

/**
 * jsdom has no layout, so the breakpoint the shell reads has to be stated. Desktop is the
 * default here because it is the layout most of these assertions are about; the two
 * below-`lg` tests set it themselves.
 */
function setViewport(desktop: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: desktop && query.includes('min-width'),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

/** Lets the promises a mocked request resolved with be turned into rendered state. */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

function sidebar() {
  return within(screen.getByRole('navigation', { name: 'Navigazione principale' }))
}

beforeEach(() => {
  setViewport(true)
  mockAuth.role = 'admin'
  mockAuth.id = undefined
  mockRoute.pathname = '/app/customers'
  mockRoute.search = ''
  localStorage.clear()
  sessionStorage.clear()
  GET.mockReset()
  POST.mockReset()
  mockGo.mockReset()
  // No other space by default: the switcher's rows are opt-in per test, the same
  // silent-empty shape the login chooser's own tests use.
  GET.mockResolvedValue({ data: [], response: { status: 200 } })
})

describe('AppShell', () => {
  it('is exactly as tall as its parent, never as tall as the page', () => {
    const { container } = renderShell()
    const root = container.firstElementChild as HTMLElement
    expect(root.className).toMatch(/\bh-full\b/)
    expect(root.className).toMatch(/\boverflow-hidden\b/)
    expect(root.className).not.toMatch(/\bh-dvh\b/)
  })

  it('reads Home, three captioned sections, then Agenti and Impostazioni at the foot (REB-873)', () => {
    renderShell()
    const nav = sidebar()
    // The links in the order the brief draws them; «Impostazioni» is a button that opens
    // its sub-items, so it is not among them.
    expect(nav.getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Home',
      'Attività',
      'Calendario',
      'Avvisi',
      'Proposte',
      'Clienti',
      'Persone',
      'Deal',
      'Fatture',
      'Ore',
      'Giornate',
      'Agenti',
    ])
    expect(nav.getAllByRole('group').map((group) => group.getAttribute('aria-label'))).toEqual([
      'Da fare',
      'Vendite',
      'Amministrazione',
    ])
    expect(within(nav.getByRole('group', { name: 'Da fare' })).getAllByRole('link').map((l) => l.textContent)).toEqual([
      'Attività',
      'Calendario',
      'Avvisi',
      'Proposte',
    ])
    expect(within(nav.getByRole('group', { name: 'Vendite' })).getAllByRole('link').map((l) => l.textContent)).toEqual([
      'Clienti',
      'Persone',
      'Deal',
    ])
    expect(within(nav.getByRole('group', { name: 'Amministrazione' })).getAllByRole('link').map((l) => l.textContent)).toEqual([
      'Fatture',
      'Ore',
      'Giornate',
    ])
    // Agents and Settings come after every section, Settings last.
    const lastLink = nav.getByRole('link', { name: 'Agenti' })
    const settings = nav.getByRole('button', { name: 'Impostazioni' })
    expect(lastLink.compareDocumentPosition(settings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    for (const caption of ['Da fare', 'Vendite', 'Amministrazione']) {
      expect(nav.getByText(caption)).toBeInTheDocument()
    }
  })

  it('has no entry for Oggi, Primi passi, Token or «Collega un agente»: they moved', () => {
    renderShell()
    const nav = sidebar()
    for (const name of ['Oggi', 'Primi passi', 'Token', 'Collega un agente']) {
      expect(nav.queryByRole('link', { name })).not.toBeInTheDocument()
      expect(nav.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('button', { name: 'Collega un agente' })).not.toBeInTheDocument()
  })

  it('points Agenti at the token route and Attività at the page Oggi now lives on', () => {
    renderShell()
    expect(sidebar().getByRole('link', { name: 'Agenti' })).toHaveAttribute('href', '/app/token')
    expect(sidebar().getByRole('link', { name: 'Attività' })).toHaveAttribute('href', '/app/activities')
  })

  it('draws the captions as plain text, never as controls, with every entry on screen', async () => {
    // Whatever route is current: nothing is folded away, so there is nothing to open.
    mockRoute.pathname = '/app/hours'
    renderShell()
    const nav = sidebar()
    for (const caption of ['Da fare', 'Vendite', 'Amministrazione']) {
      expect(nav.queryByRole('button', { name: caption })).not.toBeInTheDocument()
      expect(nav.getByText(caption).closest('button')).toBeNull()
    }
    for (const entry of ['Attività', 'Clienti', 'Persone', 'Deal', 'Fatture', 'Ore', 'Giornate']) {
      expect(nav.getByRole('link', { name: entry })).toBeInTheDocument()
    }
    // A click on a caption does nothing, and nothing about the sidebar is written down.
    await userEvent.click(nav.getByText('Vendite'))
    expect(nav.getByRole('link', { name: 'Clienti' })).toBeInTheDocument()
    expect(localStorage.length).toBe(0)
  })

  it('shows the overdue count beside «Attività» (REB-725, REB-873)', async () => {
    mockAuth.id = 'u-1'
    GET.mockImplementation(async (path: string) =>
      path === '/api/activities/today'
        ? { data: { overdue: { total: 3, items: [] } }, response: { status: 200 } }
        : { data: [], response: { status: 200 } },
    )
    renderShell()

    const tasksLink = await sidebar().findByRole('link', { name: /^Attività\s*3 in ritardo$/ })
    expect(tasksLink).toBeInTheDocument()
    // Asked for the signed-in user's own, a single row: the sidebar wants the number.
    expect(GET).toHaveBeenCalledWith('/api/activities/today', {
      params: { query: { assigned_to: 'u-1', include_unassigned: true, limit: 1 } },
    })
  })

  it('draws no count beside «Attività» when nothing is overdue', async () => {
    mockAuth.id = 'u-1'
    GET.mockImplementation(async (path: string) =>
      path === '/api/activities/today'
        ? { data: { overdue: { total: 0, items: [] } }, response: { status: 200 } }
        : { data: [], response: { status: 200 } },
    )
    renderShell()

    await waitFor(() =>
      expect(GET).toHaveBeenCalledWith('/api/activities/today', expect.anything()),
    )
    // The answer has to have been rendered, not only asked for, before an absence means anything.
    await settle()
    expect(sidebar().getByRole('link', { name: 'Attività' })).toHaveAccessibleName('Attività')
  })

  it('offers the global search in the sidebar, with the shortcut visible', () => {
    renderShell()
    const search = screen.getByRole('button', { name: /cerca/i })
    expect(search).toBeInTheDocument()
    // Spec §4: the search field sits under the brand and shows its shortcut, so it is
    // discoverable without trying the keyboard.
    expect(search).toHaveTextContent(/K/)
  })

  it('names the search landmark, so it is not one of two unlabelled regions', () => {
    // A `role="search"` with no accessible name is announced as "search" and nothing
    // else; the page's own filter rows will grow more of them.
    renderShell()
    expect(screen.getByRole('search', { name: 'Ricerca globale' })).toBeInTheDocument()
  })

  it('keeps the current page marked while the URL carries search parameters', () => {
    // What the 1440 screenshot of this pass caught: on Home the sidebar's Home entry was
    // not highlighted at all, because the dashboard writes its tab and period into the
    // URL (`routes/app/index.tsx`) and `includeSearch` defaults to true.
    mockRoute.pathname = '/app'
    mockRoute.search = '?tab=commerciale&da=2026-09-01&a=2026-09-30'
    renderShell()
    expect(sidebar().getByRole('link', { name: 'Home' })).toHaveAttribute('aria-current', 'page')
  })

  it('keeps a sub-item marked while its list carries a filter in the URL', () => {
    mockRoute.pathname = '/app/customers'
    mockRoute.search = '?q=acme'
    renderShell()
    expect(sidebar().getByRole('link', { name: 'Clienti' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('has no «Analisi» entry, expanded or collapsed', async () => {
    // The section left the interface with the 2026-09-09 revision: the estimate it was
    // read for is the «Stima fiscale» card in Home, and the two reports it also carried
    // are gone from the UI (the API and the MCP tools are untouched). Asserted in both
    // states, because the rail draws its own list of links and an entry surviving only
    // there would be invisible to a check on the expanded sidebar alone.
    renderShell()
    expect(sidebar().queryByRole('link', { name: 'Analisi' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    expect(sidebar().queryByRole('link', { name: 'Analisi' })).not.toBeInTheDocument()
  })

  it('opens the search palette from the sidebar field', async () => {
    renderShell()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /cerca/i }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('marks the entry of the current route, and not its neighbours', () => {
    renderShell()
    const nav = sidebar()
    expect(nav.getByRole('link', { name: 'Clienti' })).toHaveAttribute('aria-current', 'page')
    // A detail page under the section keeps the section marked, which is why the
    // entry is matched by prefix and not by equality.
    expect(nav.getByRole('link', { name: 'Persone' })).not.toHaveAttribute('aria-current')
  })

  it('keeps «Impostazioni» closed away from the settings, with its sub-items out of the DOM', () => {
    renderShell()
    expect(sidebar().getByRole('button', { name: 'Impostazioni' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(sidebar().queryByRole('link', { name: 'Personale' })).not.toBeInTheDocument()
  })

  it('opens «Impostazioni» on a click and closes it on a second one', async () => {
    renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Impostazioni' }))
    expect(sidebar().getByRole('button', { name: 'Impostazioni' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(sidebar().getByRole('link', { name: 'Personale' })).toBeInTheDocument()
    await userEvent.click(sidebar().getByRole('button', { name: 'Impostazioni' }))
    expect(sidebar().queryByRole('link', { name: 'Personale' })).not.toBeInTheDocument()
    // Not stored: it is a press of this visit, not a preference.
    expect(localStorage.length).toBe(0)
  })

  it('lets you close «Impostazioni» on one of its own pages, so the chevron is never a no-op', async () => {
    // The route opens it, but does not nail it open: a header whose aria-expanded cannot
    // change is a control that lies about being one.
    mockRoute.pathname = '/app/settings/profile'
    renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Impostazioni' }))
    expect(sidebar().getByRole('button', { name: 'Impostazioni' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(sidebar().queryByRole('link', { name: 'Personale' })).not.toBeInTheDocument()
  })

  it('lists one sub-item per settings group, each linking to its first section', async () => {
    // Against the shared `SETTINGS_GROUPS` and not against labels typed out here: a group
    // added to the settings page and forgotten in the sidebar is exactly the drift this
    // list exists to prevent.
    renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Impostazioni' }))
    const subItems = sidebar()
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href')?.startsWith('/app/settings/'))
    expect(subItems.map((link) => link.textContent)).toEqual(
      SETTINGS_GROUPS.map((group) => group.label),
    )
    expect(subItems.map((link) => link.getAttribute('href'))).toEqual([
      '/app/settings/profile',
      '/app/settings/space',
      '/app/settings/fields',
      '/app/settings/legal-entities',
      '/app/settings/gmail',
      '/app/settings/reminders',
    ])
  })

  it('opens Impostazioni when the current route is one of its tabs', () => {
    mockRoute.pathname = '/app/settings/rates'
    renderShell()
    const nav = sidebar()
    expect(nav.getByRole('button', { name: 'Impostazioni' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    const current = nav.getByRole('link', { name: 'Fatturazione e costi' })
    expect(current).toHaveAttribute('aria-current', 'page')
    expect(current).toHaveAttribute('data-status', 'active')
    expect(nav.getByRole('link', { name: 'Personale' })).not.toHaveAttribute('aria-current')
  })

  it.each([
    ['collaborator', ['Personale', 'Collegamenti'], ['/app/settings/profile', '/app/settings/drive']],
    ['readonly', ['Personale'], ['/app/settings/profile']],
  ])('shows a %s only the Impostazioni groups it can open', async (role, labels, hrefs) => {
    // The group is there for every role (REB-771); each role sees only the groups with a
    // section it may open, so no link would fail on click.
    mockAuth.role = role as string
    renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Impostazioni' }))
    const subItems = sidebar()
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href')?.startsWith('/app/settings/'))
    expect(subItems.map((link) => link.textContent)).toEqual(labels)
    expect(subItems.map((link) => link.getAttribute('href'))).toEqual(hrefs)
  })

  it.each(['admin', 'collaborator', 'readonly'])(
    'links the collapsed rail\'s Impostazioni icon to Profilo for a %s',
    async (role) => {
      mockAuth.role = role
      renderShell()
      await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
      expect(sidebar().getByRole('link', { name: 'Impostazioni' })).toHaveAttribute(
        'href',
        '/app/settings/profile',
      )
    },
  )

  it.each(['collaborator', 'readonly'])(
    'keeps the %s navigation surface intact: every entry and the three sections',
    (role) => {
      // REB-294 hid controls, never navigation a role may read -- the readonly person's
      // reading surface is unchanged. Asserted for both non-admin roles in one test
      // because they are meant to see the same shell; only Impostazioni separates them,
      // and that is pinned by its own test above.
      mockAuth.role = role
      renderShell()
      const nav = sidebar()
      for (const label of ['Home', 'Attività', 'Calendario', 'Avvisi', 'Proposte', 'Agenti']) {
        expect(nav.getByRole('link', { name: label })).toBeInTheDocument()
      }
      for (const label of ['Da fare', 'Vendite', 'Amministrazione']) {
        expect(nav.getByRole('group', { name: label })).toBeInTheDocument()
      }
      expect(nav.getByRole('button', { name: 'Impostazioni' })).toBeInTheDocument()
    },
  )

  /**
   * A personal access token is not an admin setting -- `PatService` scopes it by
   * `actor.id`, not role -- so unlike Impostazioni «Agenti» is not gated and not one of
   * its sub-items. Checked for both non-admin roles the backend has, since
   * "collaboratore" and "readonly" are two different guard checks that could each
   * independently regress.
   */
  it.each(['collaborator', 'readonly'])('shows Agenti to a %s, not just to an admin', (role) => {
    mockAuth.role = role
    renderShell()
    expect(sidebar().getByRole('link', { name: 'Agenti' })).toBeInTheDocument()
  })

  /**
   * The Reminders page (REB-734) is every role's own list, so it sits outside any gate: in
   * «Da fare», right after the calendar and right before Proposals, which stays the last
   * entry of the section (REB-750) and opens `/app/proposals`.
   */
  it.each(['admin', 'collaborator', 'readonly'])('shows Avvisi to a %s, between Calendario and Proposte', (role) => {
    mockAuth.role = role
    renderShell()
    const links = sidebar().getAllByRole('link')
    const names = links.map((link) => link.textContent)
    expect(names.indexOf('Avvisi')).toBe(names.indexOf('Calendario') + 1)
    expect(names.indexOf('Proposte')).toBe(names.indexOf('Avvisi') + 1)
    expect(links[names.indexOf('Avvisi')]).toHaveAttribute('href', '/app/reminders')
    expect(links[names.indexOf('Proposte')]).toHaveAttribute('href', '/app/proposals')
  })

  it('keeps Avvisi and Proposte one click away in the collapsed icon rail', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    expect(sidebar().getByRole('link', { name: 'Avvisi' })).toHaveAttribute('href', '/app/reminders')
    expect(sidebar().getByRole('link', { name: 'Proposte' })).toHaveAttribute('href', '/app/proposals')
  })

  /** What the API answers for the list: two open, one snoozed and one acknowledged. */
  function answerReminders(statuses: Array<'open' | 'snoozed' | 'acknowledged'>) {
    const list = statuses.map((status, index) => ({
      key: `task_due:${index}`,
      type: 'task_due',
      label: 'Attività in scadenza',
      severity: 'warning',
      measure: 0,
      title: `Attività ${index}`,
      detail: '',
      entity: 'task',
      entity_id: `3f0c2a10-0000-4000-8000-00000000000${index}`,
      customer_id: null,
      status,
      snoozed_until: status === 'snoozed' ? '2026-10-27' : null,
      delivered_at: null,
    }))
    GET.mockImplementation((path: string) =>
      Promise.resolve({ data: path === '/api/reminders' ? list : [], response: { status: 200 } }),
    )
  }

  it('counts only the open reminders on the Avvisi entry, not the snoozed or acknowledged ones', async () => {
    answerReminders(['open', 'snoozed', 'open', 'acknowledged'])
    renderShell()
    const entry = await sidebar().findByRole('link', { name: /Avvisi/ })
    await waitFor(() => expect(entry).toHaveTextContent('2'))
    expect(entry).toHaveAccessibleName('Avvisi, 2 da leggere')
  })

  it('draws no badge when nothing is open', async () => {
    answerReminders(['snoozed', 'acknowledged'])
    renderShell()
    await waitFor(() => expect(GET).toHaveBeenCalledWith('/api/reminders'))
    expect(sidebar().getByRole('link', { name: 'Avvisi' })).toHaveTextContent(/^Avvisi$/)
  })

  it('draws no badge when the list fails to load', async () => {
    GET.mockImplementation((path: string) =>
      Promise.resolve(
        path === '/api/reminders'
          ? { error: { detail: 'Il database non risponde.' }, response: { status: 500 } }
          : { data: [], response: { status: 200 } },
      ),
    )
    renderShell()
    await waitFor(() => expect(GET).toHaveBeenCalledWith('/api/reminders'))
    expect(sidebar().getByRole('link', { name: 'Avvisi' })).toHaveTextContent(/^Avvisi$/)
  })

  it('keeps the count on the icon in the collapsed rail, and in the link name', async () => {
    answerReminders(['open', 'open', 'open'])
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    const entry = await sidebar().findByRole('link', { name: 'Avvisi, 3 da leggere' })
    expect(entry).toHaveAttribute('href', '/app/reminders')
    // The number itself is drawn on the icon's corner, not as a label beside it.
    expect(within(entry).getByText('3')).toHaveClass('absolute')
  })

  it('caps a long count at 99+', async () => {
    answerReminders(Array.from({ length: 120 }, () => 'open' as const))
    renderShell()
    const entry = await sidebar().findByRole('link', { name: /Avvisi/ })
    await waitFor(() => expect(within(entry).getByText('99+')).toBeInTheDocument())
  })

  it('collapses to an icon rail: the captions are separators, every entry still one click away', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    const nav = sidebar()
    // No caption text in the rail, a separator in its place, and the group keeps its name.
    for (const caption of ['Da fare', 'Vendite', 'Amministrazione']) {
      expect(nav.queryByText(caption)).not.toBeInTheDocument()
      expect(nav.getByRole('group', { name: caption })).toBeInTheDocument()
    }
    expect(nav.queryByRole('button', { name: 'Vendite' })).not.toBeInTheDocument()
    for (const label of [
      'Home',
      'Attività',
      'Calendario',
      'Clienti',
      'Persone',
      'Deal',
      'Fatture',
      'Ore',
      'Giornate',
      'Agenti',
    ]) {
      expect(nav.getByRole('link', { name: label })).toBeInTheDocument()
    }
    // ...except the settings tabs, which are tabs of one page and collapse to one link.
    expect(nav.getByRole('link', { name: 'Impostazioni' })).toBeInTheDocument()
    expect(nav.queryByRole('link', { name: 'Campi' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Espandi il menu' })).toBeInTheDocument()
  })

  it('keeps the overdue count on the Attività icon in the collapsed rail', async () => {
    mockAuth.id = 'u-1'
    GET.mockImplementation(async (path: string) =>
      path === '/api/activities/today'
        ? { data: { overdue: { total: 2, items: [] } }, response: { status: 200 } }
        : { data: [], response: { status: 200 } },
    )
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    const entry = await sidebar().findByRole('link', { name: /^Attività\s*2 in ritardo$/ })
    expect(within(entry).getByText('2', { selector: '[aria-hidden]' }).parentElement).toHaveClass('absolute')
  })

  it('draws a focus ring the dark sidebar can actually show', () => {
    // shadcn's Button sets `outline-none` and a Watermelon/50 ring, which over Prussian
    // Blue is 1.73:1 -- i.e. no visible focus at all. Every control in here asks for
    // `--sidebar-ring` (Paper) instead.
    renderShell()
    expect(sidebar().getByRole('link', { name: 'Home' })).toHaveClass(
      'focus-visible:ring-sidebar-ring',
    )
    expect(screen.getByRole('button', { name: 'Comprimi il menu' })).toHaveClass(
      'focus-visible:ring-sidebar-ring',
    )
  })

  it('is a rail below lg, whose expanded form is an overlay you can dismiss', async () => {
    // 272px of the 390px a phone has is not a layout, it is a menu.
    setViewport(false)
    renderShell()
    expect(sidebar().queryByText('Vendite')).not.toBeInTheDocument()
    expect(sidebar().getByRole('link', { name: 'Clienti' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Espandi il menu' }))
    expect(sidebar().getByText('Vendite')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Chiudi il menu' }))
    expect(sidebar().queryByText('Vendite')).not.toBeInTheDocument()
  })

  it('closes the overlay when the route changes, so it never covers the page you asked for', async () => {
    setViewport(false)
    const { refresh } = renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Espandi il menu' }))
    expect(sidebar().getByText('Vendite')).toBeInTheDocument()

    mockRoute.pathname = '/app/hours'
    refresh()
    expect(sidebar().queryByText('Vendite')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Chiudi il menu' })).not.toBeInTheDocument()
  })

  it('has no overlay on a desktop viewport: the sidebar is a column beside the page', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    expect(screen.queryByRole('button', { name: 'Chiudi il menu' })).not.toBeInTheDocument()
    expect(sidebar().getByRole('link', { name: 'Impostazioni' })).toBeInTheDocument()
  })

  it('renders its children in the page area', () => {
    renderShell(<p>contenuto</p>)
    expect(within(screen.getByRole('main')).getByText('content')).toBeInTheDocument()
  })

  it('makes the page itself the one scroller, with no inset frame around it', () => {
    // REB-328: the hub's panel (a `lg:p-3` grid-ground inset, a `lg:border`, and a
    // wrapper carrying the scroll between the sidebar and `<main>`) read as a card
    // scrolling on its own inside the page. `<main>` is now a direct child of the shell
    // root and is what scrolls, full width from the sidebar to the edge of the window.
    const { container } = renderShell(<p>contenuto</p>)
    const main = screen.getByRole('main')
    expect(main.className).toContain('overflow-y-auto')
    expect(main.className).toContain('min-w-0')
    expect(main.className).toContain('bg-card')
    expect(main.className).not.toMatch(/rounded|lg:border|lg:p-/)
    // Identity, not class names: the parent IS the shell root, so no wrapper -- inert
    // or otherwise -- can sit between the sidebar and the page again.
    expect(main.parentElement).toBe(container.firstElementChild)
  })

  it('says the role in Italian under the name, not the stored enum', () => {
    // «admin» is what the API stores, not a word the product writes: every other place
    // that shows a role writes it out (`features/settings/UsersPanel.tsx`), and the 1440
    // screenshot of this pass had the raw enum sitting under Ivan's name.
    renderShell()
    expect(screen.getByRole('button', { name: 'Menu del profilo' })).toHaveTextContent(
      'amministratore',
    )
  })

  it('keeps the profile menu', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    expect(await screen.findByRole('menuitem', { name: /esci/i })).toBeInTheDocument()
  })

  /**
   * The profile tab is inside «Impostazioni», whose «Personale» entry is the same page;
   * this menu entry is the shortest way to their own profile
   * from inside the app, and the weekly report's opt-out link (spec 2026-09-16 §3.6) was
   * the only way to it from outside. Checked for both non-admin roles, as «Token» is:
   * "collaboratore" and "readonly" are two different guards.
   *
   * Queried as a link inside the open menu, not as a `menuitem`: the `Link` stand-in at
   * the top of this file renders a plain anchor and drops the props Radix clones onto
   * its child, the role among them. Scoping to the menu is what keeps this from finding
   * the settings sub-item of the same name that an admin also has in the sidebar.
   */
  it.each(['admin', 'collaborator', 'readonly'])(
    'offers «Profilo» to a %s, linking to the profile tab',
    async (role) => {
      mockAuth.role = role
      renderShell()

      await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))

      const menu = within(await screen.findByRole('menu'))
      expect(menu.getByRole('link', { name: 'Profilo' })).toHaveAttribute(
        'href',
        '/app/settings/profile',
      )
    },
  )

  /**
   * REB-457: a collaboratore's Drive is their own credential, and the Drive tab is where
   * every consent outcome lands, so the menu that holds the account's own things offers
   * it to them. Read from the same tab table as the sidebar: a readonly person, who
   * cannot start a consent, is not offered it, and an admin keeps it where it was, in
   * the sidebar's «Impostazioni».
   */
  it('offers «Google Drive» to a collaboratore in the profile menu', async () => {
    mockAuth.role = 'collaborator'
    renderShell()

    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))

    const menu = within(await screen.findByRole('menu'))
    expect(menu.getByRole('link', { name: 'Google Drive' })).toHaveAttribute(
      'href',
      '/app/settings/drive',
    )
  })

  it.each(['admin', 'readonly'])('offers no «Google Drive» in the profile menu to a %s', async (role) => {
    mockAuth.role = role
    renderShell()

    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))

    const menu = within(await screen.findByRole('menu'))
    expect(menu.getByRole('link', { name: 'Profilo' })).toBeInTheDocument()
    expect(menu.queryByRole('link', { name: 'Google Drive' })).not.toBeInTheDocument()
  })

  it('leaves «Impostazioni dello spazio» to an admin', async () => {
    mockAuth.role = 'collaborator'
    renderShell()

    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))

    const menu = within(await screen.findByRole('menu'))
    expect(menu.getByRole('link', { name: 'Profilo' })).toBeInTheDocument()
    expect(menu.queryByRole('link', { name: 'Impostazioni dello spazio' })).not.toBeInTheDocument()
  })

  /**
   * «Primi passi» left the sidebar for the profile menu (REB-873), and only once the space
   * has data: until then the Home is that very page. The predicate is the Home's own, the
   * four work reads (`useSpaceHasData`), so a customer, a deal, an hour or a document is
   * what turns the entry on.
   */
  const WORK_PATHS = ['/api/customers', '/api/deals', '/api/time-entries', '/api/documents']
  function answerWork(has: string | null) {
    GET.mockImplementation((path: string) =>
      Promise.resolve({
        data: WORK_PATHS.includes(path)
          ? { items: path === has ? [{ id: 'x' }] : [], next_cursor: null }
          : [],
        response: { status: 200 },
      }),
    )
  }

  it.each(['admin', 'collaborator', 'readonly'])(
    'offers «Primi passi» to a %s in the profile menu once the space has data',
    async (role) => {
      mockAuth.role = role
      answerWork('/api/customers')
      renderShell()

      await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
      const menu = within(await screen.findByRole('menu'))
      expect(await menu.findByRole('link', { name: 'Primi passi' })).toHaveAttribute(
        'href',
        '/app/get-started',
      )
    },
  )

  it.each(WORK_PATHS)(
    'turns «Primi passi» on when the space has any work at %s, the Home\'s own predicate',
    async (path) => {
      answerWork(path)
      renderShell()
      await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
      const menu = within(await screen.findByRole('menu'))
      expect(await menu.findByRole('link', { name: 'Primi passi' })).toBeInTheDocument()
    },
  )

  it('offers no «Primi passi» while the space is empty: the Home is that page', async () => {
    answerWork(null)
    renderShell()
    // Let the four reads be asked and answered before asserting an absence: while they are
    // pending the verdict is unknown, which also keeps the entry out of the menu.
    for (const path of WORK_PATHS) {
      await waitFor(() => expect(GET).toHaveBeenCalledWith(path, expect.anything()))
    }
    await settle()

    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    expect(menu.getByRole('link', { name: 'Profilo' })).toBeInTheDocument()
    expect(menu.queryByRole('link', { name: 'Primi passi' })).not.toBeInTheDocument()
  })
})

/**
 * The bottom-of-sidebar space switcher (REB-345 follow-up, 2026-09-25): reachable
 * without logging out, mirroring the login chooser's own shape (REB-377).
 */
describe('the space switcher', () => {
  it('lists another space the identity may enter, excluding the one already open', async () => {
    GET.mockResolvedValue({
      data: [
        { slug: 'studio', role: 'admin' },
        { slug: 'other', role: 'collaborator' },
      ],
      response: { status: 200 },
    })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    expect(await menu.findByRole('menuitem', { name: /altro/ })).toBeInTheDocument()
    expect(menu.queryByRole('menuitem', { name: /^studio/ })).not.toBeInTheDocument()
  })

  it('enters another space on click and navigates to its own basepath', async () => {
    GET.mockResolvedValue({ data: [{ slug: 'other', role: 'collaborator' }], response: { status: 200 } })
    POST.mockResolvedValue({
      data: { id: 'u1', email: 'ada@altro.it', nome: 'Ada', role: 'collaborator' },
      response: { status: 200 },
    })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    await userEvent.click(await menu.findByRole('menuitem', { name: /altro/ }))
    await waitFor(() =>
      expect(POST).toHaveBeenCalledWith('/api/identity/enter/{slug}', {
        params: { path: { slug: 'other' } },
      }),
    )
    await waitFor(() => expect(mockGo).toHaveBeenCalledWith('/altro/app/'))
  })

  it('reports the API sentence when entering another space fails', async () => {
    GET.mockResolvedValue({ data: [{ slug: 'other', role: 'admin' }], response: { status: 200 } })
    POST.mockResolvedValue({
      error: { detail: 'Questo spazio non esiste, o non lo hai mai raggiunto.' },
      response: { status: 404 },
    })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    await userEvent.click(await menu.findByRole('menuitem', { name: /altro/ }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Questo spazio non esiste, o non lo hai mai raggiunto.',
      ),
    )
  })

  it('offers to create a new space from the menu, leaving the signed-in email in the handoff, not the URL', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    await userEvent.click(await menu.findByRole('menuitem', { name: 'Crea un nuovo spazio' }))
    expect(mockGo).toHaveBeenCalledWith('/app/register')
    expect(readAndClearRegisterHandoffEmail()).toBe('m@example.com')
  })

  it('refreshes the space list when the menu opens, not only once on mount', async () => {
    // Greptile, PR #419: AppShell stays mounted for a whole session, so a space
    // gained after mount (an invitation accepted in another tab) must not be stuck
    // on the response the very first, mount-time fetch happened to return.
    let spacesCalls = 0
    GET.mockImplementation((path: string) => {
      if (path === '/api/identity/spaces') {
        spacesCalls += 1
        return Promise.resolve({
          data: spacesCalls === 1 ? [] : [{ slug: 'other', role: 'collaborator' }],
          response: { status: 200 },
        })
      }
      return Promise.resolve({ data: { slug: null }, response: { status: 200 } })
    })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    expect(await menu.findByRole('menuitem', { name: /altro/ })).toBeInTheDocument()
    expect(spacesCalls).toBeGreaterThanOrEqual(2)
  })

  // REB-678: an invitation whose mail never came is in the switcher too, and choosing it
  // goes to the acceptance page rather than entering a space this address has no seat in.
  it('lists an invitation beside the spaces, marked as one', async () => {
    GET.mockResolvedValue({
      data: [{ slug: 'rebase', role: 'collaborator', invitation: true, invited_from: 'Ivan' }],
      response: { status: 200 },
    })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    expect(await menu.findByRole('menuitem', { name: /rebase/ })).toHaveTextContent('Invito')
  })

  it('opens an invitation on the space’s own acceptance page, entering nothing', async () => {
    GET.mockResolvedValue({
      data: [{ slug: 'rebase', role: 'collaborator', invitation: true }],
      response: { status: 200 },
    })
    POST.mockResolvedValue({
      data: { url: '/rebase/app/invite?t=abc' },
      response: { status: 200 },
    })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))
    const menu = within(await screen.findByRole('menu'))
    await userEvent.click(await menu.findByRole('menuitem', { name: /rebase/ }))
    await waitFor(() =>
      expect(POST).toHaveBeenCalledWith('/api/identity/invite/{slug}', {
        params: { path: { slug: 'rebase' } },
      }),
    )
    await waitFor(() => expect(mockGo).toHaveBeenCalledWith('/rebase/app/invite?t=abc'))
    expect(POST).not.toHaveBeenCalledWith('/api/identity/enter/{slug}', expect.anything())
  })
})

describe('the azienda selector (REB-625)', () => {
  const HUMANCRAFT = { id: 'a1', nome: 'humancraft', predefinita: true, active: true } as LegalEntityRecord
  const REBASE = { id: 'a2', nome: 'rebase', predefinita: false, active: true } as LegalEntityRecord

  function twoLegalEntities(selected: string | null): LegalEntityValue {
    return {
      legal_entities: [HUMANCRAFT, REBASE],
      selected,
      select: vi.fn(),
      several: true,
      scoped: false,
      pinned: false,
      byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
    }
  }

  it('is not drawn in a one-legal-entity space, which is every space until milestone 5', () => {
    renderShell()
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })

  it('sits between the search and the navigation, offers «Tutte le aziende» first and selects by id', async () => {
    const value = twoLegalEntities(null)
    renderShell(<div />, value)
    const trigger = screen.getByRole('combobox', { name: 'Azienda' })
    expect(trigger).toHaveTextContent('Tutte le aziende')
    // After the search landmark and before the navigation one, in document order.
    const search = screen.getByRole('search', { name: 'Ricerca globale' })
    const nav = screen.getByRole('navigation', { name: 'Navigazione principale' })
    expect(search.compareDocumentPosition(trigger) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(trigger.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    await userEvent.click(trigger)
    const options = screen.getAllByRole('option').map((option) => option.textContent)
    expect(options).toEqual(['Tutte le aziende', 'humancraft', 'rebase'])
    await userEvent.click(screen.getByRole('option', { name: 'rebase' }))
    expect(value.select).toHaveBeenCalledWith('a2')
  })

  it('shows the selected azienda and goes back to every one of them', async () => {
    const value = twoLegalEntities('a2')
    renderShell(<div />, value)
    const trigger = screen.getByRole('combobox', { name: 'Azienda' })
    expect(trigger).toHaveTextContent('rebase')
    await userEvent.click(trigger)
    await userEvent.click(screen.getByRole('option', { name: 'Tutte le aziende' }))
    expect(value.select).toHaveBeenCalledWith(null)
  })

  it('draws a scoped person’s one azienda as a label, not a control (REB-635)', () => {
    const value: LegalEntityValue = {
      legal_entities: [REBASE],
      selected: 'a2',
      select: vi.fn(),
      several: false,
      scoped: true,
      pinned: true,
      byId: (id) => (id === 'a2' ? REBASE : undefined),
    }
    renderShell(<div />, value)
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
    const label = screen.getByLabelText('Azienda')
    expect(label).toHaveTextContent('rebase')
    const search = screen.getByRole('search', { name: 'Ricerca globale' })
    const nav = screen.getByRole('navigation', { name: 'Navigazione principale' })
    expect(search.compareDocumentPosition(label) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(label.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
