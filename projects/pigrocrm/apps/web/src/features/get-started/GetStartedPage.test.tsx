/**
 * «Get started» (ORB-180): the assistant card until this user has a token, the four
 * steps with their state read from six one-row requests, and the visit remembered for
 * the Home's one-time redirect. The API client is stubbed by path, `useAuth` by role.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
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
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, className }: { to: string; children: React.ReactNode; className?: string }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}))

const EMPTY_PAGE = { items: [], next_cursor: null }
/** A space that has done nothing yet: every list empty, no emitter, no token. */
const EMPTY: Record<string, unknown> = {
  '/api/customers': EMPTY_PAGE,
  '/api/deals': EMPTY_PAGE,
  '/api/time-entries': EMPTY_PAGE,
  '/api/documents': EMPTY_PAGE,
  '/api/tokens': [],
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

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <GetStartedPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
  answers()
})

afterEach(() => {
  window.localStorage.clear()
  auth.user = { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin', attivo: true }
})

describe('Get started', () => {
  it('shows the assistant card and the four steps to do on an empty space, and remembers the visit', async () => {
    renderPage()
    // The prompts are fixed text (ORB-188): the page reads no space settings to pick them.
    expect(api.GET).not.toHaveBeenCalledWith('/api/settings/space')
    expect(screen.getByRole('heading', { name: 'Get started' })).toBeInTheDocument()
    expect(await screen.findByText('Il CRM che lavora al posto tuo')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Collega l.assistente/ })).toHaveAttribute('href', '/app/token')
    expect(screen.getByText(/0 di 4/)).toBeInTheDocument()
    expect(screen.getAllByText('Da fare:')).toHaveLength(4)
    expect(screen.getByRole('link', { name: 'I tuoi dati fiscali' })).toHaveAttribute(
      'href',
      '/app/settings/issuer',
    )
    expect(window.localStorage.getItem('pigrocrm.get-started.visto:/:u1')).toBe('1')
  })

  it('counts a step done when the thing exists, and only then', async () => {
    answers({
      '/api/emitter': { ragione_sociale: 'Ada', partita_iva: '01234567890', codice_fiscale: null },
      '/api/customers': { items: [{ id: 'c1' }], next_cursor: null },
    })
    renderPage()
    expect(await screen.findByText(/2 di 4/)).toBeInTheDocument()
    expect(screen.getAllByText('Fatto:')).toHaveLength(2)
    expect(screen.queryByRole('link', { name: 'Il primo cliente' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Il primo deal, o le prime ore' })).toBeInTheDocument()
  })

  it('counts a step it could not read as done rather than nag about a broken request', async () => {
    answers({}, { '/api/customers': 500 })
    renderPage()
    expect(await screen.findByText(/1 di 4/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Il primo cliente' })).toBeNull()
  })

  it('keeps the steps, all ticked, and drops the card once everything is done and the assistant is connected', async () => {
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
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/tokens'))
    expect(screen.queryByText('Il CRM che lavora al posto tuo')).toBeNull()
  })

  it('shows a readonly user the steps as text, none of them a link', async () => {
    if (auth.user) auth.user.ruolo = 'readonly'
    renderPage()
    expect(await screen.findByText(/0 di 4/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Il primo cliente' })).toBeNull()
    expect(screen.getAllByText('Lo fa chi può scrivere nello spazio.')).toHaveLength(4)
  })

  it('does not link a collaboratore to the fiscal settings only an admin can open', async () => {
    if (auth.user) auth.user.ruolo = 'collaboratore'
    renderPage()
    expect(await screen.findByText(/0 di 4/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'I tuoi dati fiscali' })).toBeNull()
    expect(screen.getByText(/Li imposta l.amministratore/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Il primo cliente' })).toBeInTheDocument()
  })

  it('marks nothing while nobody is logged in', async () => {
    auth.user = null
    renderPage()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(window.localStorage.length).toBe(0)
  })

  it('offers a prompt to copy for each step still to do, and one for the first conversation', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    answers({ '/api/customers': { items: [{ id: 'c1' }], next_cursor: null } })
    renderPage()
    await screen.findByText(/1 di 4/)
    // Three steps to do, one done: three step prompts, each named after its step, plus
    // the card's own.
    expect(screen.getAllByText(/^Prompt per l’assistente: /)).toHaveLength(3)
    expect(screen.getByText('Il primo prompt, appena collegato')).toBeInTheDocument()
    expect(screen.getByText(INTRO_PROMPT)).toBeInTheDocument()
    expect(screen.queryByText(STEP_PROMPTS.cliente)).toBeNull()
    // The prompt is one click away, behind the step's disclosure.
    const summary = screen.getByText('Prompt per l’assistente: I tuoi dati fiscali')
    const details = summary.closest('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    await userEvent.click(summary)
    expect(details.open).toBe(true)
    expect(screen.getByText(STEP_PROMPTS.fiscali)).toBeInTheDocument()
    await userEvent.click(within(details).getByRole('button', { name: 'Copia il prompt' }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(STEP_PROMPTS.fiscali)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Prompt copiato'))
  })

  it('gives a collaboratore no fiscal prompt, since the fiscal step is the admin’s', async () => {
    if (auth.user) auth.user.ruolo = 'collaboratore'
    renderPage()
    await screen.findByText(/0 di 4/)
    expect(screen.queryByText(/Prompt per l’assistente: I tuoi dati fiscali/)).toBeNull()
    expect(screen.getByText('Prompt per l’assistente: Il primo cliente')).toBeInTheDocument()
  })

  it('says so when the clipboard is refused, and leaves the text on the page', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('no')) } })
    renderPage()
    await screen.findByText(/0 di 4/)
    await userEvent.click(screen.getByText('Il primo prompt, appena collegato'))
    await userEvent.click(screen.getAllByRole('button', { name: 'Copia il prompt' })[0] as HTMLElement)
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(screen.getByText(INTRO_PROMPT)).toBeInTheDocument()
  })

  it('survives a browser with no clipboard at all, as on a plain http origin', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    Object.assign(navigator, { clipboard: undefined })
    renderPage()
    await screen.findByText(/0 di 4/)
    await userEvent.click(screen.getByText('Il primo prompt, appena collegato'))
    await userEvent.click(screen.getAllByRole('button', { name: 'Copia il prompt' })[0] as HTMLElement)
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(screen.getByText(INTRO_PROMPT)).toBeInTheDocument()
  })

  it('gives a readonly user no prompt: there is nothing they could ask the assistant to change', async () => {
    if (auth.user) auth.user.ruolo = 'readonly'
    renderPage()
    await screen.findByText(/0 di 4/)
    expect(screen.queryAllByText(/^Prompt per l’assistente/)).toHaveLength(0)
  })
})
