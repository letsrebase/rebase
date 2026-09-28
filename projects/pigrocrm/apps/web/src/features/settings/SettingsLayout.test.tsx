import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsLayout } from './SettingsLayout'

const mockAuth = vi.hoisted(() => ({ isAdmin: true, readonly: false }))
// REB-294: the tab strip now filters through `canSeeSettingsTab`, which reads the
// session's `ruolo` -- so the stub carries a user whose role agrees with `isAdmin`,
// and the suite's existing `isAdmin` switch keeps its whole meaning. A non-admin is a
// collaboratore unless `readonly` says otherwise (REB-457 tells the two apart on the
// Drive tab).
function ruolo(): 'admin' | 'collaboratore' | 'readonly' {
  if (mockAuth.isAdmin) return 'admin'
  return mockAuth.readonly ? 'readonly' : 'collaboratore'
}
vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => mockAuth.isAdmin,
  useAuth: () => ({ user: { ruolo: ruolo() } }),
  useCanWrite: () => ruolo() !== 'readonly',
}))

// Defaults to the admin suite's usual page; the two REB-221 tests below point this at
// `/app/settings/profile` instead, the one path a non-admin may also reach.
const mockLocation = vi.hoisted(() => ({
  pathname: '/app/settings/fields',
  search: {} as Record<string, unknown>,
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    Link: ({ children, to, ...props }: { children: React.ReactNode; to?: string }) => (
      <a href={to} {...props}>
        {children}
      </a>
    ),
    Outlet: () => <div data-testid="outlet-content" />,
    useRouterState: () => ({
      location: { pathname: mockLocation.pathname, search: mockLocation.search },
    }),
  }
})

describe('SettingsLayout (the /app/settings route guard)', () => {
  beforeEach(() => {
    mockAuth.readonly = false
    mockLocation.pathname = '/app/settings/fields'
    mockLocation.search = {}
  })

  /**
   * REB-457: the Drive credential is per CRM user and the service lets any writer
   * connect it, so a collaboratore opens the Drive tab itself, where every consent
   * outcome lands. The outcome is the child route's to read (`routes/app/settings/
   * drive.tsx`), so the layout must let them through and not read it a second time.
   */
  it.each(['collegato', 'negato', 'errore', 'sessione', undefined])(
    'lets a collaboratore into the Drive tab (esito %s), with no gate in front of it',
    (esito) => {
      mockAuth.isAdmin = false
      mockLocation.pathname = '/app/settings/drive'
      mockLocation.search = esito === undefined ? {} : { esito }
      render(<SettingsLayout />)
      expect(screen.queryByText('Accesso riservato')).not.toBeInTheDocument()
      expect(screen.getByTestId('outlet-content')).toBeInTheDocument()
      expect(screen.queryByRole('status')).toBeNull()
      expect(screen.getByRole('tab', { name: 'Google Drive' })).toHaveAttribute(
        'href',
        '/app/settings/drive',
      )
      expect(screen.getByRole('tab', { name: 'Profilo' })).toBeInTheDocument()
      expect(screen.queryByRole('tab', { name: 'Gmail' })).not.toBeInTheDocument()
      expect(screen.queryByRole('tab', { name: 'Utenti' })).not.toBeInTheDocument()
    },
  )

  it('shows a collaboratore on the profile tab the Drive tab beside it', () => {
    mockAuth.isAdmin = false
    mockLocation.pathname = '/app/settings/profile'
    render(<SettingsLayout />)
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Profilo',
      'Google Drive',
    ])
  })

  /**
   * A readonly person cannot start a Drive consent (`require_write` in
   * `GoogleDriveOAuthService.start`), so the Drive tab stays behind the gate for them.
   * What the gate gave them before REB-457 stays: an outcome that lands there anyway
   * (the callback cannot know the role) is read out above the explanation, without
   * «Riprova», which would only be refused.
   */
  it('keeps a readonly person behind the gate on the Drive tab, reading the outcome with no «Riprova»', () => {
    mockAuth.isAdmin = false
    mockAuth.readonly = true
    mockLocation.pathname = '/app/settings/drive'
    mockLocation.search = { esito: 'sessione' }
    render(<SettingsLayout />)
    expect(screen.getByRole('status')).toHaveTextContent(
      'La sessione è scaduta mentre eri su Google, quindi Drive non è stato collegato.',
    )
    expect(screen.queryByRole('link', { name: 'Riprova' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Accesso riservato' })).toBeInTheDocument()
    expect(screen.queryByTestId('outlet-content')).toBeNull()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('shows a readonly person on the profile tab no Drive tab', () => {
    mockAuth.isAdmin = false
    mockAuth.readonly = true
    mockLocation.pathname = '/app/settings/profile'
    render(<SettingsLayout />)
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Profilo'])
  })

  it('shows an admin the Drive tab as before, with every other tab', () => {
    mockAuth.isAdmin = true
    mockLocation.pathname = '/app/settings/drive'
    mockLocation.search = { esito: 'collegato' }
    render(<SettingsLayout />)
    expect(screen.getByTestId('outlet-content')).toBeInTheDocument()
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getAllByRole('tab')).toHaveLength(14)
    expect(screen.getByRole('tab', { name: 'Google Drive' })).toBeInTheDocument()
  })

  it('reads no outcome on another tab, nor on the Drive tab without one', () => {
    mockAuth.isAdmin = false
    mockAuth.readonly = true
    mockLocation.pathname = '/app/settings/fields'
    mockLocation.search = { esito: 'sessione' }
    const { unmount } = render(<SettingsLayout />)
    expect(screen.queryByRole('status')).toBeNull()
    unmount()
    mockLocation.pathname = '/app/settings/drive'
    mockLocation.search = {}
    render(<SettingsLayout />)
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Accesso riservato' })).toBeInTheDocument()
  })

  it('opens with its title as the page heading, from PageHeader', () => {
    // The shell has had no top bar since the 2026-09-08 revision, so the page's own
    // `<h1>` is the only thing naming the screen.
    mockAuth.isAdmin = true
    render(<SettingsLayout />)
    expect(screen.getByRole('heading', { level: 1, name: 'Impostazioni' })).toBeInTheDocument()
  })

  it('renders the tabs and the active child route for an admin', () => {
    mockAuth.isAdmin = true
    render(<SettingsLayout />)
    expect(screen.getByRole('tab', { name: 'Campi' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Pipeline' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Utenti' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Profilo' })).toBeInTheDocument()
    expect(screen.getByTestId('outlet-content')).toBeInTheDocument()
  })

  /**
   * The three slice-4 tabs. They are asserted here and not only in their own panel
   * tests because a panel that renders correctly but is unreachable is not shipped:
   * every service behind them is admin-only, so this list is the only door they have.
   */
  it('offers the time-tracking settings tabs to an admin', () => {
    mockAuth.isAdmin = true
    render(<SettingsLayout />)
    expect(screen.getByRole('tab', { name: 'Categorie costo' })).toHaveAttribute(
      'href',
      '/app/settings/cost-categories',
    )
    expect(screen.getByRole('tab', { name: 'Tariffe' })).toHaveAttribute(
      'href',
      '/app/settings/rates',
    )
    expect(screen.getByRole('tab', { name: 'Periodi' })).toHaveAttribute(
      'href',
      '/app/settings/periods',
    )
  })

  /**
   * Same reason as the tabs above: `AutomationsPanel` is the only surface where the two
   * automation rules can be switched at all, and `AutomationConfigService` gates its write
   * on `require_admin`, so this list is the only door it has. A panel with its own passing
   * tests and no way in is not shipped.
   */
  it('offers the automations settings tab to an admin', () => {
    mockAuth.isAdmin = true
    render(<SettingsLayout />)
    expect(screen.getByRole('tab', { name: 'Automazioni' })).toHaveAttribute(
      'href',
      '/app/settings/automations',
    )
  })

  it('no longer offers a Token tab here — it moved to its own, non-admin-gated route', () => {
    mockAuth.isAdmin = true
    render(<SettingsLayout />)
    expect(screen.queryByRole('tab', { name: 'Token' })).not.toBeInTheDocument()
  })

  /**
   * This task's second named gap, and the reason a fix round called out that
   * it had no test at all: without this, a collaboratore/readonly session
   * that opens this URL directly mounts every admin panel, each firing its
   * own query that comes back 403. Asserting `Outlet` never renders is the
   * one check that actually rules that out -- asserting the message alone
   * would still pass if the panels rendered invisibly behind it.
   */
  it('shows "Accesso riservato" and never renders the child route for a non-admin', () => {
    mockAuth.isAdmin = false
    render(<SettingsLayout />)
    expect(screen.getByText('Accesso riservato')).toBeInTheDocument()
    expect(screen.queryByTestId('outlet-content')).not.toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('gives a non-admin a way back to the dashboard, not a silent bounce', () => {
    mockAuth.isAdmin = false
    render(<SettingsLayout />)
    expect(screen.getByRole('link', { name: 'Torna alla dashboard' })).toHaveAttribute(
      'href',
      '/app',
    )
  })

  it('no longer tells a non-admin they need an admin for a token, since they don’t', () => {
    mockAuth.isAdmin = false
    render(<SettingsLayout />)
    expect(screen.queryByText(/token/i)).not.toBeInTheDocument()
  })

  /**
   * REB-221's second round: the weekly digest's own opt-out link (spec 2026-09-16
   * §3.6) sends a non-admin recipient to `/app/settings/profile`, so this one
   * path must not hit the wall above. `queryByText('Accesso riservato')` absent is
   * as load-bearing as `outlet-content` present: a fix that rendered both would
   * still look broken to whoever followed the link.
   */
  it('lets a non-admin reach the profilo tab, and no admin-only tab', () => {
    mockAuth.isAdmin = false
    mockLocation.pathname = '/app/settings/profile'
    render(<SettingsLayout />)
    expect(screen.queryByText('Accesso riservato')).not.toBeInTheDocument()
    expect(screen.getByTestId('outlet-content')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Profilo' })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Utenti' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Campi' })).not.toBeInTheDocument()
  })

  it('still refuses a non-admin who tries any other settings path', () => {
    mockAuth.isAdmin = false
    mockLocation.pathname = '/app/settings/users'
    render(<SettingsLayout />)
    expect(screen.getByText('Accesso riservato')).toBeInTheDocument()
    expect(screen.queryByTestId('outlet-content')).not.toBeInTheDocument()
  })
})
