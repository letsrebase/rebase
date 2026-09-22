import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from '@rebase/ui/sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { UsersPanel } from './UsersPanel'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/lib/auth', () => ({ useAuth: vi.fn() }))

function sessionAs(user: { id: string; ruolo: string } | null) {
  return { user, isLoading: false, login: vi.fn(), logout: vi.fn() } as never
}

function ok(data: unknown, status = 200) {
  return { data, response: new Response(null, { status }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const ADMIN = {
  id: 'u1',
  email: 'admin@pigro.it',
  nome: 'Ada Admin',
  ruolo: 'admin' as const,
  attivo: true,
  created_at: '2026-08-01T10:00:00Z',
}

const DISABLED_USER = {
  id: 'u2',
  email: 'ex@pigro.it',
  nome: 'Ex Collega',
  ruolo: 'collaboratore' as const,
  attivo: false,
  created_at: '2026-08-01T10:00:00Z',
}

const OTHER_ACTIVE_USER = {
  id: 'u3',
  email: 'altro@pigro.it',
  nome: 'Altro Utente',
  ruolo: 'collaboratore' as const,
  attivo: true,
  created_at: '2026-08-01T10:00:00Z',
}

const PENDING_INVITE = {
  id: 'i1',
  email: 'luca@pigro.it',
  nome: 'Luca',
  ruolo: 'collaboratore' as const,
  invited_by: 'u1',
  expires_at: '2026-09-29T10:00:00Z',
  created_at: '2026-09-22T10:00:00Z',
}

/** GET routed by path: the panel reads the members list and «Inviti in attesa» at
 *  once, and a single `mockResolvedValue` would answer both with the same document. */
function respond(routes: Record<string, () => unknown>) {
  vi.mocked(api.GET).mockImplementation((path: string) => {
    const route = routes[path]
    if (!route) throw new Error(`unexpected GET ${path}`)
    return Promise.resolve(route() as never)
  })
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <UsersPanel />
    </QueryClientProvider>,
  )
}

const lists = {
  '/api/users': () => ok([]),
  '/api/users/invites': () => ok([]),
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
  vi.mocked(toast.error).mockReset()
  vi.mocked(toast.success).mockReset()
  // Not one of the users any fixture below represents -- tests that care who
  // "you" are override this explicitly, so a test that doesn't is provably
  // exercising the not-self path, not passing by accident.
  vi.mocked(useAuth).mockReturnValue(sessionAs({ id: 'someone-else', ruolo: 'admin' }))
})

/**
 * The row's actions live behind the «⋯» menu since the 2026-09-08 revision (design spec
 * §4), so every assertion about them opens that row's menu first. The trigger is
 * labelled per row -- «Azioni per Ada Admin» -- because a table of identical «Azioni»
 * buttons is ambiguous to a screen reader and to a test alike.
 */
async function openRowMenu(nome: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Azioni per ${nome}` }))
}

describe('UsersPanel', () => {
  it('lists users with their role and status', async () => {
    respond({ ...lists, '/api/users': () => ok([ADMIN, DISABLED_USER]) })
    renderPanel()

    expect(await screen.findByText('Ada Admin')).toBeInTheDocument()
    expect(screen.getByText('Amministratore')).toBeInTheDocument()
    expect(screen.getByText('Disattivato')).toBeInTheDocument()

    await openRowMenu('Ex Collega')
    expect(screen.getByRole('menuitem', { name: 'Riattiva' })).toBeInTheDocument()
  })

  it('reports the status as a pill, on the one component every state in the product uses', async () => {
    respond({ ...lists, '/api/users': () => ok([ADMIN, DISABLED_USER]) })
    renderPanel()

    // `data-tone` rather than a class: it is what `StatusPill` puts on the element for
    // exactly this, and it survives the next restyle.
    expect(await screen.findByText('Attivo')).toHaveAttribute('data-tone', 'ink')
    expect(screen.getByText('Disattivato')).toHaveAttribute('data-tone', 'muted')
  })

  it('shows a failed list as a distinct alert, not an empty-looking table', async () => {
    // Both lists fail: the panel renders one table per list, and a healthy empty
    // invites table would still carry the `table` role -- the assertion is that
    // *no* table renders when neither has anything real to show.
    respond({
      '/api/users': () => failed({ code: 'http_error', detail: 'Il server non risponde.' }, 503),
      '/api/users/invites': () =>
        failed({ code: 'http_error', detail: 'Inviti non disponibili.' }, 503),
    })
    renderPanel()
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.map((el) => el.textContent)).toEqual(
      expect.arrayContaining(['Il server non risponde.', 'Inviti non disponibili.']),
    )
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('toggles a user back on through the real endpoint', async () => {
    respond({ ...lists, '/api/users': () => ok([DISABLED_USER]) })
    vi.mocked(api.PATCH).mockReturnValueOnce(Promise.resolve(ok({ ...DISABLED_USER, attivo: true })))
    renderPanel()

    await openRowMenu('Ex Collega')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Riattiva' }))

    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith(
        '/api/users/{user_id}',
        expect.objectContaining({
          params: { path: { user_id: 'u2' } },
          body: { attivo: true },
        }),
      ),
    )
  })

  /**
   * The dialog is the invitation, not the account: REB-290's spec removes the typed
   * password as the way a space gains a person, and this is the assertion the card
   * names -- there is no password field to type one into. The email conflict the old
   * flow rejected client-side (a duplicate user) is the server's 409 on the invite
   * now, and it lands as a banner because it names no single field.
   */
  it('opens the Invita dialog with no password field', async () => {
    respond(lists)
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /^invita$/i }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText(/Nome/)).toBeInTheDocument()
  })

  it('invites through POST /api/users/invites, sending the blank name as null', async () => {
    respond(lists)
    vi.mocked(api.POST).mockReturnValueOnce(Promise.resolve(ok(PENDING_INVITE, 201)))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /^invita$/i }))
    await userEvent.type(screen.getByLabelText('Email'), 'giulia@pigro.it')
    await userEvent.click(screen.getByRole('button', { name: 'Invia invito' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/users/invites',
        expect.objectContaining({
          body: { email: 'giulia@pigro.it', nome: null, ruolo: 'collaboratore' },
        }),
      ),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invito inviato'))
  })

  it('shows a duplicate-email conflict as a banner, since it names no single field', async () => {
    respond(lists)
    vi.mocked(api.POST).mockReturnValueOnce(
      Promise.resolve(
        failed(
          { code: 'conflict', detail: 'esiste già un invito in attesa per questa email', email: 'a@b.it' },
          409,
        ),
      ),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /^invita$/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia invito' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'esiste già un invito in attesa per questa email',
    )
  })

  describe('Inviti in attesa', () => {
    it('lists the pending invitations with their expiry', async () => {
      respond({ ...lists, '/api/users/invites': () => ok([PENDING_INVITE]) })
      renderPanel()

      expect(await screen.findByText('Inviti in attesa')).toBeInTheDocument()
      expect(await screen.findByText('Luca')).toBeInTheDocument()
      // The row's expiry renders as a date; the exact string depends on the runner's
      // timezone, so the assertion names the shape (gg/mm/aaaa, hh:mm) and not the hour.
      expect(screen.getByText(/\d{2}\/\d{2}\/\d{2,4}, \d{2}:\d{2}/)).toBeInTheDocument()
    })

    it('says so when nothing is waiting', async () => {
      respond(lists)
      renderPanel()
      expect(await screen.findByText('Nessun invito in attesa.')).toBeInTheDocument()
    })

    it('resends through the right route and refreshes the list', async () => {
      respond({ ...lists, '/api/users/invites': () => ok([PENDING_INVITE]) })
      vi.mocked(api.POST).mockReturnValueOnce(Promise.resolve(ok(PENDING_INVITE)))
      renderPanel()

      await openRowMenu('Luca')
      await userEvent.click(screen.getByRole('menuitem', { name: 'Reinvia il link' }))

      await waitFor(() =>
        expect(api.POST).toHaveBeenCalledWith(
          '/api/users/invites/{invitation_id}/resend',
          expect.objectContaining({ params: { path: { invitation_id: 'i1' } } }),
        ),
      )
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invito reinviato'))
      // The list refetches: the row's new expiry is the server's, not this cache's.
      await waitFor(() =>
        expect(
          vi
            .mocked(api.GET)
            .mock.calls.filter((call: [string]) => call[0] === '/api/users/invites'),
        ).toHaveLength(2),
      )
    })

    it('revokes through the right route and drops the row from the list', async () => {
      respond({ ...lists, '/api/users/invites': () => ok([PENDING_INVITE]) })
      vi.mocked(api.DELETE).mockReturnValueOnce(
        Promise.resolve({ data: undefined, response: new Response(null, { status: 204 }) } as never),
      )
      renderPanel()

      await openRowMenu('Luca')
      await userEvent.click(screen.getByRole('menuitem', { name: 'Revoca' }))

      await waitFor(() =>
        expect(api.DELETE).toHaveBeenCalledWith(
          '/api/users/invites/{invitation_id}',
          expect.objectContaining({ params: { path: { invitation_id: 'i1' } } }),
        ),
      )
      expect(await screen.findByText('Nessun invito in attesa.')).toBeInTheDocument()
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invito revocato'))
    })

    it('surfaces a terminal row’s 404 as the server’s sentence', async () => {
      respond({ ...lists, '/api/users/invites': () => ok([PENDING_INVITE]) })
      vi.mocked(api.DELETE).mockReturnValueOnce(
        Promise.resolve(
          failed({ code: 'not_found', detail: 'Invitation i1 non trovato', entity: 'invitation' }, 404),
        ),
      )
      renderPanel()

      await openRowMenu('Luca')
      await userEvent.click(screen.getByRole('menuitem', { name: 'Revoca' }))

      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith('Invitation i1 non trovato'),
      )
    })
  })

  /**
   * Fix-round item 3's second half: the PATCH itself succeeding is not what
   * used to lie. `useUpdateUser` used to invalidate the list and trust a
   * second `GET /api/users` to also succeed before the screen could be
   * trusted -- deactivating your own account kills the session the moment
   * the PATCH commits, so that second request came back 401 and `DataTable`
   * (by design) kept showing the stale "Attivo" row next to a toast that had
   * already promised the opposite. Writing the mutation's own response
   * straight into the cache removes the second, independently-failable
   * request entirely -- asserting `api.GET` was called exactly once is what
   * proves that, not merely that the row happens to show the right text.
   */
  it('updates the row from the mutation’s own response, with no second request that could independently fail', async () => {
    respond({
      ...lists,
      '/api/users': () => ok([OTHER_ACTIVE_USER]),
    })
    vi.mocked(api.PATCH).mockReturnValueOnce(
      Promise.resolve(ok({ ...OTHER_ACTIVE_USER, attivo: false })),
    )
    renderPanel()

    await openRowMenu('Altro Utente')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Disattiva' }))

    expect(await screen.findByText('Disattivato')).toBeInTheDocument()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Utente disattivato'))
    expect(api.GET).toHaveBeenCalledTimes(2)
  })

  it('changes another user’s role through the real endpoint', async () => {
    respond({ ...lists, '/api/users': () => ok([OTHER_ACTIVE_USER]) })
    vi.mocked(api.PATCH).mockReturnValueOnce(
      Promise.resolve(ok({ ...OTHER_ACTIVE_USER, ruolo: 'admin' })),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('combobox', { name: /ruolo di altro utente/i }))
    await userEvent.click(screen.getByRole('option', { name: 'Amministratore' }))

    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith(
        '/api/users/{user_id}',
        expect.objectContaining({
          params: { path: { user_id: 'u3' } },
          body: { ruolo: 'admin' },
        }),
      ),
    )
  })

  /**
   * Fix-round item 3's first half: with a single admin, self-deactivation
   * bricks the installation (recovery needs the `createadmin` CLI). Disabling
   * the control is cheap and rules the whole class out at the one place a
   * click could start it.
   */
  describe('acting on your own account', () => {
    it('disables "Disattiva" for your own row', async () => {
      vi.mocked(useAuth).mockReturnValue(sessionAs({ id: ADMIN.id, ruolo: 'admin' }))
      respond({ ...lists, '/api/users': () => ok([ADMIN]) })
      renderPanel()

      await openRowMenu('Ada Admin')
      // Present and disabled, never absent: an action a record cannot take *right now*
      // is a different thing from one it can never take, and a menu whose items move
      // between rows is a menu nobody learns (`RowActions`' own docstring).
      // `aria-disabled`, not `toBeDisabled()`: a Radix menu item is a `div`, and jest-dom
      // only reads the `disabled` attribute of a form control.
      expect(screen.getByRole('menuitem', { name: 'Disattiva' })).toHaveAttribute(
        'aria-disabled',
        'true',
      )
    })

    it('still allows deactivating someone else', async () => {
      vi.mocked(useAuth).mockReturnValue(sessionAs({ id: ADMIN.id, ruolo: 'admin' }))
      respond({ ...lists, '/api/users': () => ok([ADMIN, OTHER_ACTIVE_USER]) })
      renderPanel()

      await openRowMenu('Altro Utente')
      expect(screen.getByRole('menuitem', { name: 'Disattiva' })).not.toHaveAttribute(
        'aria-disabled',
        'true',
      )
    })

    it('disables the role selector for your own row too, so you cannot demote yourself out of this screen', async () => {
      vi.mocked(useAuth).mockReturnValue(sessionAs({ id: ADMIN.id, ruolo: 'admin' }))
      respond({ ...lists, '/api/users': () => ok([ADMIN]) })
      renderPanel()

      expect(await screen.findByRole('combobox', { name: /ruolo di ada admin/i })).toBeDisabled()
    })
  })
})
