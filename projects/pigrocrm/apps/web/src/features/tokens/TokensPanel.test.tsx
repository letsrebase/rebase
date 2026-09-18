import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from '@rebase/ui/sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TokensPanel } from './TokensPanel'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useBlocker } from '@tanstack/react-router'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/lib/auth', () => ({ useAuth: vi.fn() }))
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useBlocker: vi.fn() }
})

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const EXISTING_TOKEN = {
  id: 't1',
  nome: 'Claude',
  prefix: 'pgc_abc12345',
  last_used_at: null,
  revoked_at: null,
  created_at: '2026-08-06T10:00:00Z',
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TokensPanel />
    </QueryClientProvider>,
  )
}

/**
 * `useBlocker`'s real type is a three-way overload (current object form, plus
 * two deprecated ones) purely for the library's own backward compatibility --
 * this file only ever calls it one way, so asserting that shape directly is
 * more honest than fighting the overload set to make TS re-derive it, the
 * same idiom `features/deals/queries.test.tsx`'s own `ok`/`failed` helpers
 * use for an unrelated overloaded function.
 */
interface BlockerCallArgs {
  shouldBlockFn: (args?: unknown) => boolean | Promise<boolean>
  enableBeforeUnload: boolean
}

/** The most recent `shouldBlockFn`/`enableBeforeUnload` this render passed to
 *  `useBlocker` -- the one the router would actually consult right now. */
function latestBlockerArgs(): BlockerCallArgs {
  const call = vi.mocked(useBlocker).mock.calls.at(-1)?.[0] as BlockerCallArgs | undefined
  if (!call?.shouldBlockFn) throw new Error('useBlocker was not called as expected')
  return call
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.DELETE).mockReset()
  vi.mocked(toast.error).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(useBlocker).mockClear()
  vi.mocked(useAuth).mockReturnValue({
    user: {
      id: 'u1',
      email: 'admin@pigro.it',
      nome: 'Admin',
      ruolo: 'admin',
      attivo: true,
      tariffa_oraria_default: null,
      costo_orario_default: null,
      created_at: '2026-08-06T10:00:00Z',
      digest_settimanale: true,
    },
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    enterWithLink: vi.fn(),
  })
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
})

describe('TokensPanel', () => {
  it('explains what the tokens are for', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    renderPanel()
    expect(await screen.findByText(/MCP/i)).toBeInTheDocument()
  })

  it('states plainly that a token carries its owner’s full role, with no scope and no expiry', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    renderPanel()
    expect(await screen.findByText(/senza scadenza/i)).toBeInTheDocument()
    expect(screen.getByText(/creare altri amministratori/i)).toBeInTheDocument()
  })

  /**
   * The variable name is something you have to type into a shell, character for
   * character, so it is set in monospace like every other literal in the product. It lost
   * its `<code>` when the header moved into `PageHeader`, whose `description` is a plain
   * string: the sentence belongs in the body, where it can carry markup.
   */
  it('sets the environment variable in monospace, in the body and not in the header', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    renderPanel()
    const name = await screen.findByText('PIGROCRM_TOKEN')
    expect(name.tagName).toBe('CODE')
    expect(name.closest('header')).toBeNull()
  })

  it('shows only the prefix of an existing token, never the full secret', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([EXISTING_TOKEN])))
    renderPanel()
    expect(await screen.findByText('pgc_abc12345')).toBeInTheDocument()
  })

  it('offers a way to create a new token', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    renderPanel()
    expect(await screen.findByRole('button', { name: /nuovo token/i })).toBeInTheDocument()
  })

  it('shows a failed list as a distinct alert, not an empty-looking table', async () => {
    vi.mocked(api.GET).mockReturnValue(
      Promise.resolve(failed({ code: 'http_error', detail: 'Il server non risponde.' }, 503)),
    )
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  /**
   * `useTokens` is mounted the moment this route's guard (`routes/app.tsx`)
   * has already resolved a real user, so this state is not expected to be
   * reachable in production -- but the whole point of `enabled: Boolean
   * (userId)` (features/tokens/queries.ts) is that a future regression here
   * fails closed (no request, `queryKeys.tokens('')`, never fetched) rather
   * than open (a live `GET /api/tokens` under an empty-id cache key).
   */
  it('never fetches with an empty user id', async () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    enterWithLink: vi.fn(),
    })
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    renderPanel()

    await screen.findByRole('button', { name: /nuovo token/i })
    expect(api.GET).not.toHaveBeenCalled()
  })

  describe('creating a token — the one-time reveal', () => {
    async function createToken() {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
      vi.mocked(api.POST).mockReturnValueOnce(
        Promise.resolve(
          ok({
            id: 't2',
            nome: 'Claude sul portatile',
            prefix: 'pgc_zzzz9999',
            last_used_at: null,
            revoked_at: null,
            created_at: '2026-08-06T10:00:00Z',
            token: 'pgc_the-entire-raw-secret-value',
          }),
        ),
      )
      renderPanel()
      await userEvent.click(await screen.findByRole('button', { name: /nuovo token/i }))
      await userEvent.type(screen.getByLabelText('Nome'), 'Claude sul portatile')
      await userEvent.click(screen.getByRole('button', { name: 'Crea' }))
    }

    it('shows the raw token exactly once, right after creation', async () => {
      await createToken()
      expect(await screen.findByDisplayValue('pgc_the-entire-raw-secret-value')).toBeInTheDocument()
    })

    it('never gates "Crea" on the name being filled in -- the backend decides, not the client', async () => {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
      renderPanel()
      await userEvent.click(await screen.findByRole('button', { name: /nuovo token/i }))
      expect(screen.getByRole('button', { name: 'Crea' })).not.toBeDisabled()
    })

    it('gives the revealed token field an accessible name', async () => {
      await createToken()
      expect(screen.getByLabelText('Token')).toHaveValue('pgc_the-entire-raw-secret-value')
    })

    it('lets the value be copied to the clipboard', async () => {
      await createToken()
      await screen.findByDisplayValue('pgc_the-entire-raw-secret-value')
      await userEvent.click(screen.getByRole('button', { name: /copia il token/i }))
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('pgc_the-entire-raw-secret-value')
    })

    it('cannot be dismissed with Escape or by clicking outside — only the explicit button closes it', async () => {
      await createToken()
      const secret = await screen.findByDisplayValue('pgc_the-entire-raw-secret-value')

      await userEvent.keyboard('{Escape}')
      expect(screen.getByDisplayValue('pgc_the-entire-raw-secret-value')).toBeInTheDocument()

      // Radix's Dialog sets `pointer-events: none` on <body> while open, which
      // makes userEvent.click refuse to target it (correctly, for a real
      // click) -- fireEvent dispatches the raw pointerdown Radix's own
      // "outside" detection listens for, without that CSS hit-test.
      fireEvent.pointerDown(document.body)
      expect(screen.getByDisplayValue('pgc_the-entire-raw-secret-value')).toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: /ho (copiato|salvato)/i }))
      await waitFor(() => expect(secret).not.toBeInTheDocument())
    })

    /**
     * The fourth fix-round item: Escape/click-outside are not the only ways
     * to lose this value. `useBlocker`'s `shouldBlockFn` is what stands
     * between an accidental Back/Link click and a silently vanished secret;
     * this exercises the actual function this component hands the router,
     * not merely that `useBlocker` was called with *something*.
     */
    it('asks useBlocker to block leaving while the token is unconfirmed, and to allow it once the user says so', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(false)
      await createToken()

      const { shouldBlockFn, enableBeforeUnload } = latestBlockerArgs()
      expect(enableBeforeUnload).toBe(true)
      expect(await shouldBlockFn({} as never)).toBe(true) // user cancelled leaving -> block

      vi.mocked(window.confirm).mockReturnValue(true)
      expect(await shouldBlockFn({} as never)).toBe(false) // user confirmed leaving -> allow
    })

    it('does not ask to block leaving when no token is currently unconfirmed', async () => {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
      renderPanel()
      await screen.findByRole('button', { name: /nuovo token/i })

      const { shouldBlockFn, enableBeforeUnload } = latestBlockerArgs()
      expect(enableBeforeUnload).toBe(false)
      expect(await shouldBlockFn({} as never)).toBe(false)
    })

    it('stops blocking once the reveal dialog is closed through the explicit button', async () => {
      await createToken()
      await screen.findByDisplayValue('pgc_the-entire-raw-secret-value')
      await userEvent.click(screen.getByRole('button', { name: /ho (copiato|salvato)/i }))

      const { shouldBlockFn, enableBeforeUnload } = latestBlockerArgs()
      expect(enableBeforeUnload).toBe(false)
      expect(await shouldBlockFn({} as never)).toBe(false)
    })
  })

  describe('revoking a token', () => {
    /** The «⋯» at the end of the row, and the one action behind it. Revoking is an
     *  action of the row like every other in the product since the design revision
     *  (spec §4), not an inline `Trash2` this one table kept to itself. */
    async function chooseRevoke() {
      await userEvent.click(await screen.findByRole('button', { name: 'Azioni per Claude' }))
      await userEvent.click(await screen.findByRole('menuitem', { name: 'Revoca' }))
    }

    it('offers the revoke inside the row menu, in the destructive tone', async () => {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([EXISTING_TOKEN])))
      renderPanel()

      await userEvent.click(await screen.findByRole('button', { name: 'Azioni per Claude' }))
      expect(await screen.findByRole('menuitem', { name: 'Revoca' })).toHaveAttribute(
        'data-variant',
        'destructive',
      )
    })

    it('asks for confirmation before revoking, since it cannot be undone', async () => {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([EXISTING_TOKEN])))
      vi.spyOn(window, 'confirm').mockReturnValue(false)
      renderPanel()

      await chooseRevoke()
      expect(api.DELETE).not.toHaveBeenCalled()
    })

    it('revokes through the real endpoint once confirmed', async () => {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([EXISTING_TOKEN])))
      vi.mocked(api.DELETE).mockReturnValueOnce(Promise.resolve(ok(undefined)))
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      renderPanel()

      await chooseRevoke()

      await waitFor(() =>
        expect(api.DELETE).toHaveBeenCalledWith(
          '/api/tokens/{token_id}',
          expect.objectContaining({ params: { path: { token_id: 't1' } } }),
        ),
      )
      await waitFor(() => expect(toast.success).toHaveBeenCalled())
    })

    it('offers no revoke action for an already-revoked token', async () => {
      vi.mocked(api.GET).mockReturnValue(
        Promise.resolve(ok([{ ...EXISTING_TOKEN, revoked_at: '2026-08-05T00:00:00Z' }])),
      )
      renderPanel()

      expect(await screen.findByText('Revocato')).toBeInTheDocument()
      // No «⋯» at all, rather than a menu with one disabled item: the button is a promise
      // that there is something behind it (`RowActions`).
      expect(screen.queryByRole('button', { name: 'Azioni per Claude' })).not.toBeInTheDocument()
    })
  })

  /**
   * The table reads like every other table in the product since the design revision
   * (spec §4): the state is a round pill with a coloured dot, not the filled badge this
   * column used to carry, and a date wears the calendar icon.
   */
  describe('how the table reads', () => {
    it('shows an active token as a settled pill, not a filled badge', async () => {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([EXISTING_TOKEN])))
      renderPanel()
      const pill = (await screen.findByText('Attivo')).closest('[data-slot="badge"]')
      expect(pill).toHaveAttribute('data-variant', 'pill')
      expect(pill).toHaveAttribute('data-tone', 'ink')
    })

    /** Revoking is something the owner did on purpose. A list of old tokens in the
     *  destructive tint would say something went wrong when nothing did. */
    it('keeps a revoked token quiet rather than tinting it as a warning', async () => {
      vi.mocked(api.GET).mockReturnValue(
        Promise.resolve(ok([{ ...EXISTING_TOKEN, revoked_at: '2026-08-05T00:00:00Z' }])),
      )
      renderPanel()

      const pill = (await screen.findByText('Revocato')).closest('[data-slot="badge"]')
      expect(pill).toHaveAttribute('data-tone', 'muted')
    })

    /** «mai» and not the em dash: a token that has never been used is a fact about the
     *  token, and it is the fact somebody checks before revoking one. */
    it('says a token has never been used in words, with no calendar icon', async () => {
      vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([EXISTING_TOKEN])))
      renderPanel()
      const cell = (await screen.findByText('mai')).closest('td')
      expect(cell?.querySelector('svg')).toBeNull()
    })

    it('puts a calendar icon before a real last-used moment, and keeps its time of day', async () => {
      vi.mocked(api.GET).mockReturnValue(
        Promise.resolve(ok([{ ...EXISTING_TOKEN, last_used_at: '2026-08-06T10:30:00Z' }])),
      )
      renderPanel()

      const cell = (await screen.findByText(/ago 2026/)).closest('td')
      expect(cell?.querySelector('svg')).not.toBeNull()
      expect(cell?.textContent).toMatch(/\d{2}:\d{2}/)
    })
  })
})
