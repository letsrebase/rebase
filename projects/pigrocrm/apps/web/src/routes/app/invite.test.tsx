/** The page an invitation mail lands on (spec 2026-09-17 §1, REB-291). */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, createFileRoute: () => (options: unknown) => options }
})

const enterWithInvite = vi.fn()
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: null, enterWithInvite }) }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

import { api } from '@/lib/api'
import { InvitePage } from './invite'
import { stripEntraToken, takeEntraToken } from '@/lib/entra-token'

const GET = api.GET as unknown as ReturnType<typeof vi.fn>
const POST = api.POST as unknown as ReturnType<typeof vi.fn>

function peekOk(data: unknown) {
  return { data, response: { status: 200 } }
}

/** The dead states answer as the API does: a problem document whose `code` names which
 *  one, at 410 (404 for `invitation_unknown`). */
function dead(status: number, code: string, detail: string) {
  return { error: { code, detail }, response: { status } }
}

const PEEK = { spazio: 'Studio Bianchi', invitato_da: 'Ada Admin', nome: 'Luca' }
const PEEK_NO_NOME = { ...PEEK, nome: null }

/** GET routed the way the page uses it: the peek on `/api/auth/invite`, the bare
 *  page's `/api/tenants/root` answering "this installation has no name". */
function peeking(peek: unknown) {
  GET.mockImplementation((path: string) =>
    Promise.resolve(path === '/api/auth/invite' ? peekOk(peek) : peekOk({ slug: null })),
  )
}

beforeEach(() => {
  GET.mockReset()
  POST.mockReset()
  enterWithInvite.mockReset()
  enterWithInvite.mockResolvedValue(undefined)
  // Whatever a previous test left in the scrubber's one-shot slot.
  takeEntraToken()
})

describe('the invitation page', () => {
  it('peeks the invitation and shows the space and the inviter before spending', async () => {
    peeking(PEEK)
    render(<InvitePage token="abc" go={vi.fn()} />)

    expect(await screen.findByText('Entra in Studio Bianchi')).toBeInTheDocument()
    expect(screen.getByText(/Ada Admin ti ha invitato/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Entra nello spazio' })).toBeInTheDocument()
    // Reading is not spending (spec §3): no POST until the button is pressed.
    expect(POST).not.toHaveBeenCalled()
    expect(enterWithInvite).not.toHaveBeenCalled()
  })

  it('spends the invitation on the click and lands on the home', async () => {
    peeking(PEEK)
    const go = vi.fn()
    render(<InvitePage token="abc" go={go} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Entra nello spazio' }))
    await waitFor(() => expect(enterWithInvite).toHaveBeenCalledWith('abc', undefined))
    await waitFor(() => expect(go).toHaveBeenCalledWith('/app/'))
  })

  it('asks for the name only when the invitation carried none, and sends what was typed', async () => {
    peeking(PEEK_NO_NOME)
    render(<InvitePage token="abc" go={vi.fn()} />)

    const field = await screen.findByLabelText('Come ti chiami?')
    await userEvent.type(field, 'Giulia')
    await userEvent.click(screen.getByRole('button', { name: 'Entra nello spazio' }))
    await waitFor(() => expect(enterWithInvite).toHaveBeenCalledWith('abc', 'Giulia'))
  })

  it('shows no name field when the invitation already carries one', async () => {
    peeking(PEEK)
    render(<InvitePage token="abc" go={vi.fn()} />)

    await screen.findByText('Entra in Studio Bianchi')
    expect(screen.queryByLabelText('Come ti chiami?')).not.toBeInTheDocument()
    expect(screen.getByText('Ciao Luca!')).toBeInTheDocument()
  })

  /**
   * The three dead states read differently on purpose (spec §1: the oracle risk that
   * folds the magic link's failures into one sentence does not exist for a 32-byte
   * token), so each renders the API's own sentence and the way to ask the admin for a
   * new link. `invitation_used` is its own test below: it is the only one whose next
   * step is a login rather than a new link.
   */
  it('renders the expired state with its sentence and the way to ask again', async () => {
    GET.mockResolvedValue(
      dead(410, 'invitation_expired', 'questo invito è scaduto: chiedi a chi amministra'),
    )
    render(<InvitePage token="abc" go={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/scaduto/)
    expect(
      screen.getByRole('link', { name: /Torna al login e chiedi un altro invito/ }),
    ).toHaveAttribute('href', '/app/login')
    expect(screen.queryByRole('button', { name: /Entra nello spazio/ })).not.toBeInTheDocument()
  })

  it.each([
    ['invitation_revoked', 'revocato'],
    ['invitation_unknown', 'non esiste'],
  ])('renders %s as its own dead state', async (code, sentence) => {
    GET.mockResolvedValue(dead(code === 'invitation_unknown' ? 404 : 410, code, sentence))
    render(<InvitePage token="abc" go={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(sentence)
  })

  it('renders the already-used state as a login, not a new link', async () => {
    GET.mockResolvedValue(
      dead(410, 'invitation_used', 'questo invito è già stato usato: entra con la tua email'),
    )
    render(<InvitePage token="abc" go={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/già stato usato/)
    expect(screen.getByRole('link', { name: 'Entra con la tua email' })).toHaveAttribute(
      'href',
      '/app/login',
    )
  })
  it('turns into the dead state when an admin revokes between the peek and the click', async () => {
    peeking(PEEK)
    // The seam is `enterWithInvite`, and what it rejects with is what the real
    // `unwrap` throws: the problem document itself, not the fetch envelope.
    enterWithInvite.mockRejectedValueOnce({
      code: 'invitation_revoked',
      detail: 'questo invito è stato revocato',
    })
    const go = vi.fn()
    render(<InvitePage token="abc" go={go} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Entra nello spazio' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/revocato/)
    expect(go).not.toHaveBeenCalled()
  })

  it('re-peeks, not re-spends, when the peek itself fails', async () => {
    // A 429 from the peek's own rate bucket: a page failure, not a dead invitation
    // (`invitation_*` is the only family that renders the dead card).
    GET.mockResolvedValueOnce(dead(429, 'rate_limited', 'Troppo richieste, riprova.'))
    render(<InvitePage token="abc" go={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Troppo richieste')

    peeking(PEEK)
    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(await screen.findByText('Entra in Studio Bianchi')).toBeInTheDocument()
    // A failed peek left the token unspent; the retry reads again and spends nothing.
    expect(POST).not.toHaveBeenCalled()
    expect(enterWithInvite).not.toHaveBeenCalled()
  })

  it('peeks once per mount, however often the page re-renders', async () => {
    peeking(PEEK)
    const { rerender } = render(<InvitePage token="abc" go={vi.fn()} />)
    await screen.findByText('Entra in Studio Bianchi')
    rerender(<InvitePage token="abc" go={vi.fn()} />)
    await new Promise((resolve) => setTimeout(resolve, 20))
    const peeks = vi.mocked(GET).mock.calls.filter(([path]) => path === '/api/auth/invite')
    expect(peeks).toHaveLength(1)
  })
})

/**
 * The card's named PostHog assertion, in the shape REB-273/PR #174 established for the
 * magic link (the scrubber's own unit tests cover the URL rewrite itself; the shared
 * `before_send` that strips `?t=` off every event is tested in shared/analytics): on
 * `/app/invite`, taking the token out leaves the URL empty *and* the rendered page
 * holds no copy of it anywhere a pageview, autocapture or replay could read.
 */
describe('the invitation token stays out of what PostHog can see', () => {
  it('the URL carries no token once the scrub has run, and the page never re-adds it', async () => {
    window.history.replaceState(null, '', '/studio/app/invite?t=secret-token')
    stripEntraToken()
    expect(window.location.href).not.toContain('secret-token')

    peeking(PEEK)
    const { container } = render(<InvitePage token="secret-token" go={vi.fn()} />)
    await screen.findByText('Entra in Studio Bianchi')
    expect(container).not.toHaveTextContent('secret-token')
    expect(window.location.href).not.toContain('secret-token')
    takeEntraToken()
  })
})
