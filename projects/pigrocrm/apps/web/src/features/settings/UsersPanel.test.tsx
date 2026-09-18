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

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
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

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <UsersPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
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
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([ADMIN, DISABLED_USER])))
    renderPanel()

    expect(await screen.findByText('Ada Admin')).toBeInTheDocument()
    expect(screen.getByText('Amministratore')).toBeInTheDocument()
    expect(screen.getByText('Disattivato')).toBeInTheDocument()

    await openRowMenu('Ex Collega')
    expect(screen.getByRole('menuitem', { name: 'Riattiva' })).toBeInTheDocument()
  })

  it('reports the status as a pill, on the one component every state in the product uses', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([ADMIN, DISABLED_USER])))
    renderPanel()

    // `data-tone` rather than a class: it is what `StatusPill` puts on the element for
    // exactly this, and it survives the next restyle.
    expect(await screen.findByText('Attivo')).toHaveAttribute('data-tone', 'ink')
    expect(screen.getByText('Disattivato')).toHaveAttribute('data-tone', 'muted')
  })

  it('shows a failed list as a distinct alert, not an empty-looking table', async () => {
    vi.mocked(api.GET).mockReturnValue(
      Promise.resolve(failed({ code: 'http_error', detail: 'Il server non risponde.' }, 503)),
    )
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('toggles a user back on through the real endpoint', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([DISABLED_USER])))
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
   * `UserCreate.password` requires >= 10 characters server-side
   * (`MIN_PASSWORD_LENGTH`, auth/schemas.py), and the brief's own sample gated
   * "Crea" on that exact number client-side. This project's own convention (see
   * `CustomerForm`/`DealForm`/`PersonForm`: Save is disabled only while the
   * mutation is in flight, never on field content) says the backend's message is
   * what the user sees, attached to the field -- not a client-side rule
   * duplicating it. This proves both halves: the button is never gated, and a
   * too-short password's rejection lands on the password field.
   */
  it('never disables "Crea" on password length, and shows the server’s own rejection on the password field', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    vi.mocked(api.POST).mockReturnValueOnce(
      Promise.resolve(
        failed(
          {
            code: 'validation_failed',
            detail: 'deve avere almeno 10 caratteri',
            field: 'password',
            reason: 'deve avere almeno 10 caratteri',
            expected: '>= 10 caratteri',
          },
          422,
        ),
      ),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /nuovo utente/i }))
    const createButton = screen.getByRole('button', { name: 'Crea' })
    expect(createButton).not.toBeDisabled()

    await userEvent.type(screen.getByLabelText('Nome'), 'Nuovo Utente')
    await userEvent.type(screen.getByLabelText('Email'), 'nuovo@pigro.it')
    await userEvent.type(screen.getByLabelText('Password'), 'corta')
    await userEvent.click(createButton)

    // Not `/almeno 10 caratteri/` alone -- the dialog's own static help text
    // ("La password deve avere almeno 10 caratteri.") already contains that
    // exact phrase, so asserting on it would pass even if the server's message
    // never rendered at all. "(atteso: ...)" only appears in `fieldErrorFrom`'s
    // own formatting of a validation error that carries `expected`, so it can
    // only come from the field-level message this test exists to prove.
    expect(await screen.findByText(/atteso: >= 10 caratteri/)).toBeInTheDocument()
  })

  it('shows a duplicate-email conflict as a banner, since it names no single field', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    vi.mocked(api.POST).mockReturnValueOnce(
      Promise.resolve(
        failed(
          { code: 'conflict', detail: 'esiste già un utente con questa email', email: 'a@b.it' },
          409,
        ),
      ),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /nuovo utente/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('esiste già un utente con questa email')
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
    vi.mocked(api.GET).mockReturnValueOnce(Promise.resolve(ok([OTHER_ACTIVE_USER])))
    vi.mocked(api.PATCH).mockReturnValueOnce(
      Promise.resolve(ok({ ...OTHER_ACTIVE_USER, attivo: false })),
    )
    renderPanel()

    await openRowMenu('Altro Utente')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Disattiva' }))

    expect(await screen.findByText('Disattivato')).toBeInTheDocument()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Utente disattivato'))
    expect(api.GET).toHaveBeenCalledTimes(1)
  })

  it('changes another user’s role through the real endpoint', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([OTHER_ACTIVE_USER])))
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
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([ADMIN])))
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
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([ADMIN, OTHER_ACTIVE_USER])))
      renderPanel()

      await openRowMenu('Altro Utente')
      expect(screen.getByRole('menuitem', { name: 'Disattiva' })).not.toHaveAttribute(
        'aria-disabled',
        'true',
      )
    })

    it('disables the role selector for your own row too, so you cannot demote yourself out of this screen', async () => {
      vi.mocked(useAuth).mockReturnValue(sessionAs({ id: ADMIN.id, ruolo: 'admin' }))
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([ADMIN])))
      renderPanel()

      expect(await screen.findByRole('combobox', { name: /ruolo di ada admin/i })).toBeDisabled()
    })
  })
})
