import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SETTINGS_TABS } from '@/features/settings/tabs'
import { AppShell } from './AppShell'
import { SIDEBAR_GROUPS_KEY } from './sidebarGroups'

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
  }: {
    children: React.ReactNode
    to: string
    className?: string
    activeProps?: Record<string, unknown>
    activeOptions?: { exact?: boolean; includeSearch?: boolean }
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
      <a href={to} className={className} {...(active ? activeProps : {})}>
        {children}
      </a>
    )
  },
  useRouterState: () => ({ location: { pathname: mockRoute.pathname } }),
  // The command palette the shell mounts navigates; nothing here asserts on where.
  useNavigate: () => vi.fn(),
  // The connect-agent dialog the shell now mounts guards against navigating away with an
  // unsaved token; nothing here exercises the guard itself.
  useBlocker: vi.fn(),
}))

// The connect-agent dialog copies text via `sonner`'s toast; nothing here asserts on the
// toast copy, only that clicking a copy button does not throw for want of a mock.
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const mockAuth = vi.hoisted(() => ({ ruolo: 'admin' as string }))
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { nome: 'Mario', email: 'm@example.com', ruolo: mockAuth.ruolo }, logout: vi.fn() }),
  useIsAdmin: () => mockAuth.ruolo === 'admin',
}))

/**
 * The shell mounts the command palette, which is a TanStack Query consumer, so the
 * provider is part of the harness rather than of any one test. Its query is disabled
 * below three characters, so nothing here issues a request.
 */
function renderShell(children: React.ReactNode = <div />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // A fresh element on every call: re-rendering the *same* element object is a React
  // bail-out, which would make `refresh()` (the stand-in for a navigation) do nothing.
  const tree = () => (
    <QueryClientProvider client={client}>
      <AppShell>{children}</AppShell>
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

function sidebar() {
  return within(screen.getByRole('navigation', { name: 'Navigazione principale' }))
}

beforeEach(() => {
  setViewport(true)
  mockAuth.ruolo = 'admin'
  mockRoute.pathname = '/app/customers'
  mockRoute.search = ''
  localStorage.clear()
})

describe('AppShell', () => {
  it('is exactly as tall as its parent, never as tall as the page', () => {
    const { container } = renderShell()
    const root = container.firstElementChild as HTMLElement
    expect(root.className).toMatch(/\bh-full\b/)
    expect(root.className).toMatch(/\boverflow-hidden\b/)
    expect(root.className).not.toMatch(/\bh-dvh\b/)
  })

  it('shows the top-level entries and the group headers in Italian', () => {
    renderShell()
    const nav = sidebar()
    for (const label of ['Home', 'Get started', 'Token']) {
      expect(nav.getByRole('link', { name: label })).toBeInTheDocument()
    }
    // «Get started» right under Home (ORB-180), before the groups.
    expect(nav.getAllByRole('link').slice(0, 2).map((l) => l.textContent)).toEqual(['Home', 'Get started'])
    for (const label of ['Vendite', 'Amministrazione', 'Impostazioni']) {
      expect(nav.getByRole('button', { name: label })).toBeInTheDocument()
    }
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

  it('opens the group that contains the current route and marks the sub-item', () => {
    renderShell()
    const nav = sidebar()
    expect(nav.getByRole('button', { name: 'Vendite' })).toHaveAttribute('aria-expanded', 'true')
    expect(nav.getByRole('link', { name: 'Clienti' })).toHaveAttribute('aria-current', 'page')
    // A detail page under the section keeps the section marked, which is why the
    // sub-item is matched by prefix and not by equality.
    expect(nav.getByRole('link', { name: 'Persone' })).not.toHaveAttribute('aria-current')
  })

  it('keeps the other groups closed, with their sub-items out of the DOM', () => {
    renderShell()
    const nav = sidebar()
    expect(nav.getByRole('button', { name: 'Amministrazione' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(nav.queryByRole('link', { name: 'Fatture' })).not.toBeInTheDocument()
  })

  it('opens a group on click and persists it', async () => {
    const { unmount } = renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Amministrazione' }))
    expect(sidebar().getByRole('link', { name: 'Ore' })).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(SIDEBAR_GROUPS_KEY) ?? '{}')).toMatchObject({
      amministrazione: true,
    })

    // Persisted means it survives a reload, not just a re-render.
    unmount()
    renderShell()
    expect(sidebar().getByRole('button', { name: 'Amministrazione' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(sidebar().getByRole('link', { name: 'Ore' })).toBeInTheDocument()
  })

  it('closes a group on a second click and persists that too', async () => {
    localStorage.setItem(SIDEBAR_GROUPS_KEY, JSON.stringify({ amministrazione: true }))
    renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Amministrazione' }))
    expect(sidebar().queryByRole('link', { name: 'Ore' })).not.toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(SIDEBAR_GROUPS_KEY) ?? '{}')).toMatchObject({
      amministrazione: false,
    })
  })

  it('lets you close the group of the current route, so the chevron is never a no-op', async () => {
    // The route opens its own group, but it does not nail it open: a header whose
    // aria-expanded cannot change is a control that lies about being one.
    renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Vendite' }))
    expect(sidebar().getByRole('button', { name: 'Vendite' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(sidebar().queryByRole('link', { name: 'Clienti' })).not.toBeInTheDocument()
  })

  it('survives a corrupt stored preference', () => {
    localStorage.setItem(SIDEBAR_GROUPS_KEY, 'non è json')
    renderShell()
    expect(sidebar().getByRole('button', { name: 'Amministrazione' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('lists one sub-item per settings tab, in the same order', async () => {
    // Against the shared `SETTINGS_TABS` and not against a handful of labels typed out
    // here: a tab added to the settings page and forgotten in the sidebar is exactly the
    // drift this list exists to prevent.
    renderShell()
    await userEvent.click(sidebar().getByRole('button', { name: 'Impostazioni' }))
    const subItems = sidebar()
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href')?.startsWith('/app/settings/'))
    expect(subItems.map((link) => link.textContent)).toEqual(
      SETTINGS_TABS.map((tab) => tab.label),
    )
  })

  it('opens Impostazioni when the current route is one of its tabs', () => {
    mockRoute.pathname = '/app/settings/rates'
    renderShell()
    const nav = sidebar()
    expect(nav.getByRole('button', { name: 'Impostazioni' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(nav.getByRole('link', { name: 'Tariffe' })).toHaveAttribute('aria-current', 'page')
  })

  it('hides Impostazioni from a non-admin', () => {
    mockAuth.ruolo = 'collaboratore'
    renderShell()
    expect(sidebar().queryByRole('button', { name: 'Impostazioni' })).not.toBeInTheDocument()
  })

  /**
   * A personal access token is not an admin setting -- `PatService` scopes it by
   * `actor.id`, not role -- so unlike Impostazioni it stays a top-level entry, outside the
   * admin-gated group. Checked for both non-admin roles the backend has, since
   * "collaboratore" and "readonly" are two different guard checks that could each
   * independently regress.
   */
  it.each(['collaboratore', 'readonly'])('shows Token to a %s, not just to an admin', (ruolo) => {
    mockAuth.ruolo = ruolo
    renderShell()
    expect(sidebar().getByRole('link', { name: 'Token' })).toBeInTheDocument()
  })

  it('collapses to an icon rail: no group headers, every section still one click away', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    const nav = sidebar()
    expect(nav.queryByRole('button', { name: 'Vendite' })).not.toBeInTheDocument()
    // The sub-items of the collapsible groups become icon links in the rail...
    for (const label of ['Home', 'Get started', 'Clienti', 'Deal', 'Fatture', 'Ore', 'Token']) {
      expect(nav.getByRole('link', { name: label })).toBeInTheDocument()
    }
    expect(nav.getAllByRole('link', { name: 'Get started' })).toHaveLength(1)
    // ...except the settings tabs, which are tabs of one page and collapse to one link.
    expect(nav.getByRole('link', { name: 'Impostazioni' })).toBeInTheDocument()
    expect(nav.queryByRole('link', { name: 'Campi' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Espandi il menu' })).toBeInTheDocument()
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
    expect(sidebar().queryByRole('button', { name: 'Vendite' })).not.toBeInTheDocument()
    expect(sidebar().getByRole('link', { name: 'Clienti' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Espandi il menu' }))
    expect(sidebar().getByRole('button', { name: 'Vendite' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Chiudi il menu' }))
    expect(sidebar().queryByRole('button', { name: 'Vendite' })).not.toBeInTheDocument()
  })

  it('closes the overlay when the route changes, so it never covers the page you asked for', async () => {
    setViewport(false)
    const { refresh } = renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Espandi il menu' }))
    expect(sidebar().getByRole('button', { name: 'Vendite' })).toBeInTheDocument()

    mockRoute.pathname = '/app/hours'
    refresh()
    expect(sidebar().queryByRole('button', { name: 'Vendite' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Chiudi il menu' })).not.toBeInTheDocument()
  })

  it('has no overlay on a desktop viewport: the sidebar is a column beside the page', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    expect(screen.queryByRole('button', { name: 'Chiudi il menu' })).not.toBeInTheDocument()
    expect(sidebar().getByRole('link', { name: 'Impostazioni' })).toBeInTheDocument()
  })

  it('renders its children inside the content panel', () => {
    renderShell(<p>contenuto</p>)
    expect(within(screen.getByRole('main')).getByText('contenuto')).toBeInTheDocument()
  })

  it('draws the content panel square, with its line and no radius', () => {
    // It carried a literal 16px corner at the lg breakpoint until 2026-09-18, the one
    // corner the old spec drew larger than the derived card radius. The application is
    // squared now and the panel reads the shared tokens like everything else.
    renderShell(<p>contenuto</p>)
    const panel = screen.getByRole('main').parentElement!.className
    expect(panel).toContain('lg:border')
    expect(panel).not.toMatch(/rounded/)
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
   * The whole «Impostazioni» group is admin-only in the sidebar, and the profile tab is
   * inside it -- so for a non-admin this menu entry is the only way to their own profile
   * from inside the app, and the weekly report's opt-out link (spec 2026-09-16 §3.6) was
   * the only way to it from outside. Checked for both non-admin roles, as «Token» is:
   * "collaboratore" and "readonly" are two different guards.
   *
   * Queried as a link inside the open menu, not as a `menuitem`: the `Link` stand-in at
   * the top of this file renders a plain anchor and drops the props Radix clones onto
   * its child, the role among them. Scoping to the menu is what keeps this from finding
   * the settings sub-item of the same name that an admin also has in the sidebar.
   */
  it.each(['admin', 'collaboratore', 'readonly'])(
    'offers «Profilo» to a %s, linking to the profile tab',
    async (ruolo) => {
      mockAuth.ruolo = ruolo
      renderShell()

      await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))

      const menu = within(await screen.findByRole('menu'))
      expect(menu.getByRole('link', { name: 'Profilo' })).toHaveAttribute(
        'href',
        '/app/settings/profile',
      )
    },
  )

  it('leaves «Impostazioni dello spazio» to an admin', async () => {
    mockAuth.ruolo = 'collaboratore'
    renderShell()

    await userEvent.click(screen.getByRole('button', { name: 'Menu del profilo' }))

    const menu = within(await screen.findByRole('menu'))
    expect(menu.getByRole('link', { name: 'Profilo' })).toBeInTheDocument()
    expect(menu.queryByRole('link', { name: 'Impostazioni dello spazio' })).not.toBeInTheDocument()
  })

  /**
   * «Collega un agente» is not gated by role, for the same reason Token is not: the token
   * it mints belongs to whoever creates it, not to the space.
   */
  it.each(['admin', 'collaboratore', 'readonly'])('offers «Collega un agente» to a %s, above the profile', (ruolo) => {
    mockAuth.ruolo = ruolo
    renderShell()
    const button = screen.getByRole('button', { name: 'Collega un agente' })
    expect(button).toBeInTheDocument()
    // Above the profile block: the button comes before the profile trigger in the DOM.
    const profile = screen.getByRole('button', { name: 'Menu del profilo' })
    expect(button.compareDocumentPosition(profile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('opens the connect dialog from the sidebar', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Collega un agente' }))
    expect(screen.getByRole('dialog', { name: 'Collega un agente' })).toBeInTheDocument()
  })

  it('keeps the entry in the rail as an icon with its name', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: 'Comprimi il menu' }))
    expect(screen.getByRole('button', { name: 'Collega un agente' })).toBeInTheDocument()
  })
})
