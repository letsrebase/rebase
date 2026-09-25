/**
 * «Primi passi», the start page of spec 2026-09-16 §4 (REB-222): three blocks, the Gmail
 * door in each of its states, the assistant inline until a token exists, and the four
 * steps with their screen and their prompt. The API client is stubbed by path, `useAuth`
 * by role, and every test gets a new `QueryClient`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from '@rebase/ui/sonner'
import { api } from '@/lib/api'
import { GetStartedPage } from './GetStartedPage'
import { INTRO_PROMPT, STEP_PROMPTS } from './prompts'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

const auth: { user: { id: string; email: string; nome: string; ruolo: string; attivo: boolean } | null } = {
  user: { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin', attivo: true },
}
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    user: auth.user,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    enterWithLink: vi.fn(),
  }),
  useCanWrite: () => auth.user?.ruolo !== 'readonly',
  // The invoice door's own gate (REB-224): registering an issued invoice is the admin's.
  useCan: () => auth.user?.ruolo === 'admin',
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode } & Record<string, unknown>) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
  // The token guard the assistant block shares with the dialog; what it asks is
  // `ConnectAgentDialog.test.tsx`'s subject.
  useBlocker: vi.fn(),
}))

const EMPTY_PAGE = { items: [], next_cursor: null }
const NO_GMAIL = { configured: false, account: null, banner: null, banner_text: null, missing_scopes: [] }
const GMAIL_READY = { ...NO_GMAIL, configured: true }
const ACCOUNT = {
  id: 'g1',
  email_address: 'ada@studio.it',
  status: 'active',
  scopes_granted: [],
  gmail_store_bodies: true,
  last_sync_at: null,
  last_error: null,
  last_error_at: null,
  consent_expires_at: null,
  disconnected_at: null,
}
/** A space that has done nothing yet: every list empty, no emitter, no token, and no
 *  Google client, which is where every space starts. */
const EMPTY: Record<string, unknown> = {
  '/api/customers': EMPTY_PAGE,
  '/api/deals': EMPTY_PAGE,
  '/api/time-entries': EMPTY_PAGE,
  '/api/documents': EMPTY_PAGE,
  '/api/tokens': [],
  '/api/gmail/account': NO_GMAIL,
}

/** Stubs by path: `overrides` win, `/api/emitter` is 404 unless overridden, `failing`
 *  answers that HTTP status with an error body. */
function answers(overrides: Record<string, unknown> = {}, failing: Record<string, number> = {}) {
  vi.mocked(api.GET).mockImplementation(
    ((path: string) =>
      Promise.resolve(
        path in failing
          ? { error: { detail: 'boom' }, response: new Response(null, { status: failing[path] }) }
          : path in overrides
            ? { data: overrides[path], response: new Response(null, { status: 200 }) }
            : path === '/api/emitter'
              ? { error: { detail: 'Not Found' }, response: new Response(null, { status: 404 }) }
              : { data: EMPTY[path], response: new Response(null, { status: 200 }) },
      )) as never,
  )
}

function renderPage(esito?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <GetStartedPage esito={esito} />
    </QueryClientProvider>,
  )
}

/** The block a heading belongs to, so a query cannot wander into the next one. */
function block(name: string): HTMLElement {
  const heading = screen.getByRole('heading', { name })
  return heading.closest('[data-slot="card"]') as HTMLElement
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
  answers()
})

afterEach(() => {
  auth.user = { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin', attivo: true }
})

describe('the start page', () => {
  it('shows the three blocks on an empty space, the assistant inline and the four steps to do', async () => {
    renderPage()
    expect(screen.getByRole('heading', { name: 'Primi passi' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Porta dentro il tuo lavoro' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Fai lavorare l’assistente' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Oppure a mano' })).toBeInTheDocument()
    // The connection itself, not a link to it: the endpoint and the token are right here.
    const assistant = block('Fai lavorare l’assistente')
    expect(within(assistant).getByLabelText('Endpoint')).toHaveValue(`${window.location.origin}/mcp`)
    expect(within(assistant).getByRole('button', { name: 'Crea il token' })).toBeInTheDocument()
    expect(within(assistant).getByText('Il primo prompt, appena collegato')).toBeInTheDocument()
    expect(screen.getByText(/0 di 4/)).toBeInTheDocument()
    expect(screen.getAllByText('Da fare:')).toHaveLength(4)
    // The prompts are fixed text (ORB-188): the page reads no space settings to pick them.
    expect(api.GET).not.toHaveBeenCalledWith('/api/settings/space')
  })

  it('remembers nothing in the browser: the one-time redirect of ORB-180 is gone', async () => {
    window.localStorage.clear()
    renderPage()
    await screen.findByRole('heading', { name: 'Oppure a mano' })
    expect(window.localStorage.length).toBe(0)
  })
})

describe('the Gmail door', () => {
  it('says what a space without a Google client needs, and sends an admin to Impostazioni → Spazio', async () => {
    renderPage()
    const door = within(await screen.findByRole('region', { name: 'Collega Gmail' }))
    expect(await door.findByText(/serve un client OAuth di Google/)).toBeInTheDocument()
    expect(door.getByRole('link', { name: 'Impostazioni → Spazio' })).toHaveAttribute('href', '/app/settings/space')
    // No button that would only answer 409.
    expect(door.queryByRole('link', { name: 'Collega Gmail' })).toBeNull()
  })

  it('tells a collaboratore who sets the client, with no link to a page only an admin opens', async () => {
    if (auth.user) auth.user.ruolo = 'collaboratore'
    renderPage()
    const door = within(await screen.findByRole('region', { name: 'Collega Gmail' }))
    expect(await door.findByText(/lo imposta l’amministratore dello spazio/)).toBeInTheDocument()
    expect(door.queryByRole('link')).toBeNull()
  })

  it('offers the consent flow as a plain link once the space has a client', async () => {
    answers({ '/api/gmail/account': GMAIL_READY })
    renderPage()
    const door = within(await screen.findByRole('region', { name: 'Collega Gmail' }))
    expect(await door.findByRole('link', { name: 'Collega Gmail' })).toHaveAttribute(
      'href',
      '/api/gmail/oauth/start',
    )
  })

  it('shows a connected mailbox with the customers it proposes under it (REB-223)', async () => {
    answers({
      '/api/gmail/account': { ...GMAIL_READY, account: ACCOUNT },
      '/api/gmail/customer-suggestions': [
        { dominio: 'acme.it', nome: 'Acme', conversazioni: 3, ultimo_messaggio: null, persone: [] },
      ],
    })
    renderPage()
    const door = within(await screen.findByRole('region', { name: 'Collega Gmail' }))
    expect(await door.findByText('Casella collegata')).toBeInTheDocument()
    expect(door.getByText('ada@studio.it')).toBeInTheDocument()
    expect(await door.findByRole('list', { name: 'Clienti proposti dalla casella' })).toBeInTheDocument()
    expect(door.queryByRole('link', { name: /Collega Gmail/ })).toBeNull()
  })

  it('gives a lapsed consent the server’s sentence and the way to give it again', async () => {
    answers({
      '/api/gmail/account': {
        ...GMAIL_READY,
        account: { ...ACCOUNT, status: 'revoked' },
        banner: 'revoked',
        banner_text: 'Il consenso a Gmail è stato revocato: ricollega la casella.',
      },
    })
    renderPage()
    const door = within(await screen.findByRole('region', { name: 'Collega Gmail' }))
    expect(await door.findByText(/stato revocato/)).toBeInTheDocument()
    expect(door.getByRole('link', { name: 'Ricollega Gmail' })).toHaveAttribute('href', '/api/gmail/oauth/start')
  })

  it('tells a readonly user who can connect it', async () => {
    if (auth.user) auth.user.ruolo = 'readonly'
    answers({ '/api/gmail/account': GMAIL_READY })
    renderPage()
    const door = within(await screen.findByRole('region', { name: 'Collega Gmail' }))
    expect(await door.findByText('Con un accesso in sola lettura non si collega una casella.')).toBeInTheDocument()
    expect(door.queryByRole('link')).toBeNull()
  })

  it('renders the consent flow’s outcome from the fixed table, and nothing for a code it does not know', async () => {
    answers({ '/api/gmail/account': { ...GMAIL_READY, account: ACCOUNT } })
    renderPage('collegato')
    expect(await screen.findByText('Casella Google collegata.')).toBeInTheDocument()
  })

  it('says a consent came back with no session, and offers «Riprova» beside the door', async () => {
    answers({ '/api/gmail/account': GMAIL_READY })
    renderPage('sessione')
    const door = within(await screen.findByRole('region', { name: 'Collega Gmail' }))
    expect(door.getByRole('status')).toHaveTextContent(
      'La sessione è scaduta mentre eri su Google, quindi la casella non è stata collegata.',
    )
    expect(door.getByRole('link', { name: 'Riprova' })).toHaveAttribute('href', '/api/gmail/oauth/start')
  })

  it('does not echo an esito it does not know', async () => {
    renderPage('<b>scrivi qui</b>')
    await screen.findByRole('region', { name: 'Collega Gmail' })
    expect(screen.queryByText(/scrivi qui/)).toBeNull()
  })
})

describe('the assistant block', () => {
  it('is the prompt alone once this person has a token', async () => {
    answers({ '/api/tokens': [{ id: 'k1' }] })
    renderPage()
    const assistant = within(block(await findHeading('Fai lavorare l’assistente')))
    expect(assistant.queryByLabelText('Endpoint')).toBeNull()
    expect(assistant.getByText('Il tuo assistente è collegato: ecco da dove cominciare.')).toBeInTheDocument()
    await userEvent.click(assistant.getByText('Il primo prompt, appena collegato'))
    expect(assistant.getByText(INTRO_PROMPT)).toBeInTheDocument()
  })

  it('keeps the one copy of a token it just minted on screen, though the token read now says connected', async () => {
    let tokens: unknown[] = []
    vi.mocked(api.GET).mockImplementation(
      ((path: string) =>
        Promise.resolve(
          path === '/api/tokens'
            ? { data: tokens, response: new Response(null, { status: 200 }) }
            : path === '/api/emitter'
              ? { error: { detail: 'Not Found' }, response: new Response(null, { status: 404 }) }
              : { data: EMPTY[path], response: new Response(null, { status: 200 }) },
        )) as never,
    )
    vi.mocked(api.POST).mockImplementation((() => {
      tokens = [{ id: 't2' }]
      return Promise.resolve({
        data: {
          id: 't2',
          nome: 'Claude Code',
          prefix: 'pgc_zzzz9999',
          last_used_at: null,
          revoked_at: null,
          created_at: '2026-09-23T10:00:00Z',
          token: 'pgc_il-valore-intero',
        },
        response: new Response(null, { status: 201 }),
      })
    }) as never)
    renderPage()
    const assistant = within(block(await findHeading('Fai lavorare l’assistente')))
    await userEvent.click(assistant.getByRole('button', { name: 'Crea il token' }))
    expect(await assistant.findByDisplayValue('pgc_il-valore-intero')).toBeInTheDocument()
    // The mint invalidated the token read, which now answers one token: the block would
    // shrink to the prompt, taking the token with it, if it followed that read alone.
    const tokenReads = () => vi.mocked(api.GET).mock.calls.filter((call) => (call as unknown[])[0] === '/api/tokens')
    await waitFor(() => expect(tokenReads()).toHaveLength(2))
    expect(assistant.getByDisplayValue('pgc_il-valore-intero')).toBeInTheDocument()
    expect((assistant.getByLabelText('Comando per Claude Code') as HTMLTextAreaElement).value).toContain(
      'Bearer pgc_il-valore-intero',
    )
  })
})

describe('the assistant block, once the token is copied', () => {
  it('puts the token away on «Ho copiato il token» and shrinks to the prompt', async () => {
    let tokens: unknown[] = []
    vi.mocked(api.GET).mockImplementation(
      ((path: string) =>
        Promise.resolve(
          path === '/api/tokens'
            ? { data: tokens, response: new Response(null, { status: 200 }) }
            : path === '/api/emitter'
              ? { error: { detail: 'Not Found' }, response: new Response(null, { status: 404 }) }
              : { data: EMPTY[path], response: new Response(null, { status: 200 }) },
        )) as never,
    )
    vi.mocked(api.POST).mockImplementation((() => {
      tokens = [{ id: 't2' }]
      return Promise.resolve({
        data: { id: 't2', nome: 'Claude Code', prefix: 'pgc_zzzz9999', last_used_at: null, revoked_at: null, created_at: '2026-09-23T10:00:00Z', token: 'pgc_x' },
        response: new Response(null, { status: 201 }),
      })
    }) as never)
    renderPage()
    const assistant = within(block(await findHeading('Fai lavorare l’assistente')))
    expect(assistant.queryByRole('button', { name: 'Ho copiato il token' })).toBeNull()
    await userEvent.click(assistant.getByRole('button', { name: 'Crea il token' }))
    await assistant.findByDisplayValue('pgc_x')
    await userEvent.click(assistant.getByRole('button', { name: 'Ho copiato il token' }))
    expect(assistant.queryByDisplayValue('pgc_x')).toBeNull()
    expect(assistant.queryByLabelText('Endpoint')).toBeNull()
    expect(assistant.getByText('Il primo prompt, appena collegato')).toBeInTheDocument()
  })
})

describe('the manual steps', () => {
  it('pairs each step to do with its screen and its prompt', async () => {
    renderPage()
    const steps = within(block(await findHeading('Oppure a mano')))
    expect(steps.getByRole('link', { name: 'A mano: Impostazioni → Emittente' })).toHaveAttribute(
      'href',
      '/app/settings/issuer',
    )
    expect(steps.getByRole('link', { name: 'A mano: Clienti' })).toHaveAttribute('href', '/app/customers')
    expect(steps.getAllByRole('link', { name: 'A mano: Deal' })).toHaveLength(2)
    expect(steps.getAllByRole('button', { name: 'Chiedilo all’assistente' })).toHaveLength(4)
    // Each action is described by its step, so four identical names still read apart.
    expect(steps.getAllByRole('button', { name: 'Chiedilo all’assistente' })[1]).toHaveAccessibleDescription(
      'Il primo cliente',
    )
    expect(steps.getAllByRole('link', { name: 'A mano: Deal' })[1]).toHaveAccessibleDescription('La prima offerta')
  })

  it('opens a step’s prompt under its row and copies it', async () => {
    renderPage()
    const steps = within(block(await findHeading('Oppure a mano')))
    const [fiscali] = steps.getAllByRole('button', { name: 'Chiedilo all’assistente' })
    expect(fiscali).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(STEP_PROMPTS.fiscali)).toBeNull()
    await userEvent.click(fiscali as HTMLElement)
    expect(fiscali).toHaveAttribute('aria-expanded', 'true')
    const prompt = screen.getByText(STEP_PROMPTS.fiscali)
    const body = prompt.closest('div') as HTMLElement
    await userEvent.click(within(body).getByRole('button', { name: 'Copia il prompt' }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(STEP_PROMPTS.fiscali)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Prompt copiato'))
    await userEvent.click(fiscali as HTMLElement)
    expect(screen.queryByText(STEP_PROMPTS.fiscali)).toBeNull()
  })

  it('counts a step done when the thing exists, and only then', async () => {
    answers({
      '/api/emitter': { ragione_sociale: 'Ada', partita_iva: '01234567890', codice_fiscale: null },
      '/api/customers': { items: [{ id: 'c1' }], next_cursor: null },
    })
    renderPage()
    expect(await screen.findByText(/2 di 4/)).toBeInTheDocument()
    expect(screen.getAllByText('Fatto:')).toHaveLength(2)
    expect(screen.queryByRole('link', { name: 'A mano: Clienti' })).toBeNull()
    expect(screen.getAllByRole('link', { name: 'A mano: Deal' })).toHaveLength(2)
  })

  it('does not tick «La prima offerta» for a document that is not an offer, such as an uploaded invoice', async () => {
    const base = vi.mocked(api.GET).getMockImplementation()
    vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: { tipo?: string } } }) =>
      path === '/api/documents'
        ? Promise.resolve({
            data: init?.params?.query?.tipo === 'offerta' ? EMPTY_PAGE : { items: [{ id: 'fattura-1' }], next_cursor: null },
            response: new Response(null, { status: 200 }),
          })
        : (base as (p: string, i?: unknown) => unknown)(path, init)) as never)
    renderPage()
    expect(await screen.findByText(/0 di 4/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'A mano: Deal' })).toHaveLength(2)
  })

  it('counts a step it could not read as done rather than nag about a broken request', async () => {
    answers({}, { '/api/customers': 500 })
    renderPage()
    expect(await screen.findByText(/1 di 4/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'A mano: Clienti' })).toBeNull()
  })

  it('keeps the steps, all ticked, once everything is done', async () => {
    answers({
      '/api/emitter': { ragione_sociale: 'Ada', partita_iva: null, codice_fiscale: 'RSSMRA80A01H501U' },
      '/api/customers': { items: [{ id: 'c1' }], next_cursor: null },
      '/api/time-entries': { items: [{ id: 't1' }], next_cursor: null },
      '/api/documents': { items: [{ id: 'd1' }], next_cursor: null },
      '/api/tokens': [{ id: 'k1' }],
    })
    renderPage()
    expect(await screen.findByText(/Fatti tutti/)).toBeInTheDocument()
    expect(screen.getAllByText('Fatto:')).toHaveLength(4)
    expect(screen.queryAllByRole('button', { name: 'Chiedilo all’assistente' })).toHaveLength(0)
  })

  it('shows a readonly user the steps as text, with neither a screen nor a prompt', async () => {
    if (auth.user) auth.user.ruolo = 'readonly'
    renderPage()
    expect(await screen.findByText(/0 di 4/)).toBeInTheDocument()
    expect(screen.queryAllByRole('link', { name: /^A mano/ })).toHaveLength(0)
    expect(screen.queryAllByRole('button', { name: 'Chiedilo all’assistente' })).toHaveLength(0)
    expect(screen.getAllByText('Lo fa chi può scrivere nello spazio.')).toHaveLength(4)
  })

  it('gives a collaboratore neither the fiscal screen nor its prompt, since that step is the admin’s', async () => {
    if (auth.user) auth.user.ruolo = 'collaboratore'
    renderPage()
    expect(await screen.findByText(/0 di 4/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'A mano: Impostazioni → Emittente' })).toBeNull()
    expect(screen.getByText(/Li imposta l.amministratore/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'A mano: Clienti' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Chiedilo all’assistente' })).toHaveLength(3)
  })

  it('says so when the clipboard is refused, and leaves the text on the page', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('no')) } })
    renderPage()
    await screen.findByText(/0 di 4/)
    await userEvent.click(screen.getByText('Il primo prompt, appena collegato'))
    await userEvent.click(screen.getAllByRole('button', { name: 'Copia il prompt' })[0] as HTMLElement)
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(screen.getByText(INTRO_PROMPT)).toBeInTheDocument()
  })

  it('survives a browser with no clipboard at all, as on a plain http origin', async () => {
    Object.assign(navigator, { clipboard: undefined })
    renderPage()
    await screen.findByText(/0 di 4/)
    await userEvent.click(screen.getAllByRole('button', { name: 'Chiedilo all’assistente' })[1] as HTMLElement)
    const body = screen.getByText(STEP_PROMPTS.cliente).closest('div') as HTMLElement
    await userEvent.click(within(body).getByRole('button', { name: 'Copia il prompt' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(screen.getByText(STEP_PROMPTS.cliente)).toBeInTheDocument()
  })
})

async function findHeading(name: string): Promise<string> {
  await screen.findByRole('heading', { name })
  return name
}
