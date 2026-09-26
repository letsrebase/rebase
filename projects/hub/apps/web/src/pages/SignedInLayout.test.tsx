import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminGuard } from './admin/AdminGuard'
import { Area } from './member/Area'
import { SignedInLayout } from './SignedInLayout'

vi.mock('@rebase/analytics/browser', () => ({
  capture: vi.fn(),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}))
import { identifyUser, resetUser } from '@rebase/analytics/browser'
import { ME_KEY } from '@/lib/me'

const IVAN = {
  id: 'a1',
  nome: 'Ivan',
  cognome: 'Fiore',
  email: 'ivan@rebase.it',
  linkedin_url: null,
  role: 'admin',
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
  ha_scheda: false,
  cv_filename: null,
  cv_size: null,
  tariffa_giornaliera: null,
  posizione: null,
  remoto: null,
  links: [],
  completa: false,
}
const ADA = { ...IVAN, id: 'f1', nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it', role: 'member' }

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** The real shell and both its guards -- `SignedInLayout` for "signed in at all",
 *  `AdminGuard` for "and an admin" -- mounted on a router shaped like the real one:
 *  `/me` (the real `Area`, so the access-rule message has somewhere to land) and
 *  `/admin/talent` (a stub, since the admin pages themselves are tested on their
 *  own). `/login` is a stub too: only the redirect there is this file's business. */
function mount(path = '/admin/talent') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const login = createRoute({ getParentRoute: () => root, path: '/login', component: () => <h1>Accedi</h1> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: SignedInLayout })
  const me = createRoute({ getParentRoute: () => signedIn, path: '/me', component: () => <Outlet /> })
  const ioIndex = createRoute({
    getParentRoute: () => me,
    path: '/',
    component: Area,
    validateSearch: (search: Record<string, unknown>): { negato?: true } => ({
      negato: search.negato === true || search.negato === 'true' ? true : undefined,
    }),
  })
  const adminArea = createRoute({ getParentRoute: () => signedIn, path: '/admin', component: AdminGuard })
  const adminTalent = createRoute({
    getParentRoute: () => adminArea,
    path: '/talent',
    component: () => <h1>Dentro</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([
      login,
      signedIn.addChildren([me.addChildren([ioIndex]), adminArea.addChildren([adminTalent])]),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return client
}

/** The hub's `SignedInLayout` reads the viewport through `useMediaQuery` since REB-316,
 *  and jsdom answers `matchMedia` with nothing. The same stub the CRM's `AppShell.test`
 *  carries: desktop by default (the shape most assertions here are about), with the
 *  mobile tests below setting it themselves. */
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

beforeEach(() => setViewport(true))

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('the signed-in frame and PostHog (ORB-185, REB-279)', () => {
  it('identifies an admin once the session is known, with the role', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    expect(identifyUser).toHaveBeenCalledTimes(1)
    expect(identifyUser).toHaveBeenCalledWith('a1', { email: 'ivan@rebase.it', nome: 'Ivan', ruolo: 'admin' })
  })

  it('identifies a member once, with role member (REB-280 widening, done here already)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, ADA))
    mount('/me')
    await screen.findAllByText('Ada Lovelace')
    expect(identifyUser).toHaveBeenCalledTimes(1)
    expect(identifyUser).toHaveBeenCalledWith('f1', { email: 'ada@studio.it', nome: 'Ada', ruolo: 'member' })
  })

  it('identifies the same admin once, however many times the session is fetched again', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, IVAN))
    const client = mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    await client.invalidateQueries({ queryKey: ME_KEY })
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
    expect(identifyUser).toHaveBeenCalledTimes(1)
  })

  it('identifies nobody without a session, and sends the visitor to /login', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(401, { detail: 'Autenticazione richiesta' }))
    mount()
    await screen.findByRole('heading', { name: 'Accedi' })
    expect(identifyUser).not.toHaveBeenCalled()
  })

  it('forgets the person on Esci, before the page leaves', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    // The logout ends in `window.location.assign`, which jsdom cannot do and says so
    // once on the console (from its own console, out of a spy's reach); the assertion
    // is about what happens before it.
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    await userEvent.setup().click(screen.getByRole('button', { name: /Esci/ }))
    await waitFor(() => expect(resetUser).toHaveBeenCalledTimes(1))
  })
})

describe('the sidebar, gated on role', () => {
  it('shows the admin group, its "Amministrazione" eyebrow, and "La tua area" for an admin', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    expect(screen.getByRole('link', { name: /Amministratori/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /La tua area/ })).toBeInTheDocument()
    expect(screen.getByText('Amministrazione')).toBeInTheDocument()
  })

  it('reads Talenti in place of the old Developer e CTO and Iscrizioni entries (REB-283)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    expect(screen.getByRole('link', { name: /Talenti/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Developer e CTO/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Iscrizioni/ })).toBeNull()
  })

  it('shows Match between Talenti and Aziende for an admin (REB-413)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    const match = screen.getByRole('link', { name: 'Match' })
    expect(match.getAttribute('href')).toMatch(/\/admin\/matches$/)
    const labels = screen
      .getAllByRole('link')
      .map((link) => link.textContent)
      .filter((label): label is string => ['Talenti', 'Match', 'Aziende'].includes(label ?? ''))
    expect(labels).toEqual(['Talenti', 'Match', 'Aziende'])
  })

  it('shows «Richieste team» between Match and Aziende for an admin (REB-514)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    const richieste = screen.getByRole('link', { name: 'Richieste team' })
    expect(richieste.getAttribute('href')).toMatch(/\/admin\/team$/)
    const labels = screen
      .getAllByRole('link')
      .map((link) => link.textContent)
      .filter((label): label is string => ['Match', 'Richieste team', 'Aziende'].includes(label ?? ''))
    expect(labels).toEqual(['Match', 'Richieste team', 'Aziende'])
  })

  it('shows «Talent cloud» to a person the cloud is open for, and to nobody else (REB-518)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { ...ADA, talent_cloud: true }))
    mount('/me')
    const cloud = await screen.findByRole('link', { name: 'Talent cloud' })
    expect(cloud.getAttribute('href')).toMatch(/\/me\/cloud$/)
  })

  it('leaves «Talent cloud» out while no grant is live (REB-518)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { ...ADA, talent_cloud: false }))
    mount('/me')
    await screen.findByRole('link', { name: /La tua area/ })
    expect(screen.queryByRole('link', { name: 'Talent cloud' })).toBeNull()
  })

  it('hides the admin group and its eyebrow for a member, keeping "La tua area"', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, ADA))
    mount('/me')
    await screen.findAllByText('Ada Lovelace')
    expect(screen.queryByRole('link', { name: /Amministratori/ })).toBeNull()
    expect(screen.getByRole('link', { name: /La tua area/ })).toBeInTheDocument()
    expect(screen.queryByText('Amministrazione')).toBeNull()
  })
})

describe('the frame keeps a fixed viewport height (REB-311)', () => {
  it('pins the sidebar to the viewport height and scrolls it and main on their own axes', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })

    const aside = screen.getByRole('link', { name: 'rebase' }).closest('aside')
    expect(aside).toHaveClass('h-full', 'overflow-y-auto')
    expect(aside?.parentElement).toHaveClass('h-full')
  })

  // REB-348: the CRM's own inset panel (a `lg:p-3` grid-ground margin, an `lg:border`,
  // and a wrapper carrying the scroll between the sidebar and `<main>`) was copied here
  // and read as a card scrolling on its own inside the page, the same shape REB-328
  // removed from `AppShell`. `<main>` is now the one scroller, full width from the
  // sidebar to the edge of the window; the remaining wrapper only carries the mobile
  // header above it and no longer insets or borders the page.
  it('makes the page itself the one scroller, with no inset frame around it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    const main = (await screen.findByRole('heading', { name: 'Dentro' })).closest('main')
    expect(main).not.toBeNull()
    expect(main!.className).toContain('overflow-y-auto')
    expect(main!.className).toContain('bg-card')
    expect(main!.className).not.toMatch(/rounded|lg:border|lg:p-/)
    // The wrapper above it (which also carries the mobile header) is a plain column,
    // not an inset or bordered panel.
    expect(main!.parentElement!.className).toContain('min-w-0')
    expect(main!.parentElement!.className).toContain('overflow-hidden')
    expect(main!.parentElement!.className).not.toMatch(/lg:p-|lg:border/)
  })
})

describe('/admin/* access rule (REB-279, closes REB-106)', () => {
  it('sends a signed-in non-admin to /me with a sentence, never a blank frame or a login form', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, ADA))
    mount('/admin/talent')
    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('riservata a chi amministra')
    expect(screen.queryByRole('heading', { name: 'Dentro' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Accedi' })).toBeNull()
  })

  it('lets an admin straight into /admin/*', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount('/admin/talent')
    expect(await screen.findByRole('heading', { name: 'Dentro' })).toBeInTheDocument()
  })
})

describe('the mobile shell (REB-316)', () => {
  it('replaces the sidebar column with a menu trigger below the breakpoint', async () => {
    setViewport(false)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    // The fixed `w-56` aside starved every field below ~600px (the card's bug): on a
    // phone there is no aside at all, and the nav lives behind the trigger.
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(screen.queryByText('Amministrazione')).toBeNull()
    expect(screen.getByRole('button', { name: 'Apri il menu' })).toBeInTheDocument()
  })

  it('renders the admin nav in the drawer once opened, at its 44px targets', async () => {
    setViewport(false)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    const user = userEvent.setup()
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    // Grab the trigger before opening: Radix hides the control behind an open dialog,
    // so the role query only sees it while the drawer is closed.
    const trigger = screen.getByRole('button', { name: 'Apri il menu' })
    await user.click(trigger)

    const drawer = await screen.findByRole('dialog', { name: 'Menu di navigazione' })
    expect(within(drawer).getByRole('link', { name: /Talenti/ })).toBeInTheDocument()
    expect(within(drawer).getByText('Amministrazione')).toBeInTheDocument()
    expect(within(drawer).getByRole('button', { name: /Esci/ })).toBeInTheDocument()
    // Every row carries the 44px floor the card names, not the desktop 32/36px sizes.
    const link = within(drawer).getByRole('link', { name: /Talenti/ })
    expect(link.className).toMatch(/(^| )min-h-11( |$)/)
    expect(trigger.className).toMatch(/(^| )size-11( |$)/)
  })

  it('closes the drawer on Escape', async () => {
    setViewport(false)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    const user = userEvent.setup()
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    await user.click(screen.getByRole('button', { name: 'Apri il menu' }))
    await screen.findByRole('dialog', { name: 'Menu di navigazione' })
    await user.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Menu di navigazione' })).toBeNull(),
    )
  })

  it('closes the drawer and navigates when a destination is tapped', async () => {
    setViewport(false)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    const user = userEvent.setup()
    mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Dentro' })
    await user.click(screen.getByRole('button', { name: 'Apri il menu' }))
    const drawer = await screen.findByRole('dialog', { name: 'Menu di navigazione' })
    await user.click(within(drawer).getByRole('link', { name: /La tua area/ }))
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Menu di navigazione' })).toBeNull(),
    )
  })

  it('shows no trigger and keeps the aside on a desktop viewport', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, IVAN))
    mount()
    await screen.findByRole('heading', { name: 'Dentro' })
    expect(screen.queryByRole('button', { name: 'Apri il menu' })).toBeNull()
    expect(screen.getByRole('link', { name: 'rebase' }).closest('aside')).toBeInTheDocument()
  })
})
