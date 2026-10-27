import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SidebarNav } from '@/components/SidebarNav'
import { AdminModelliAi } from './ModelliAi'

const ME = { nome: 'Ivan', cognome: 'Sala', email: 'ivan@rebase.it', role: 'admin' } as never
const SECRET = 'sk-secret-abcd'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const provider = (name: string, extra = {}) => ({
  provider: name,
  configured: false,
  key_hint: null,
  enabled: false,
  saved_at: null,
  saved_by_name: null,
  ...extra,
})
const task = (name: string, extra = {}) => ({
  task: name,
  provider: 'anthropic',
  model: 'claude-opus-5',
  effort: 'medium',
  max_output_tokens: 2000,
  daily_cap_eur: null,
  enabled: true,
  spent_today_eur: '0.00',
  cap_reached: false,
  reason: null,
  ...extra,
})
const CONFIG = {
  secrets_key_present: true,
  providers: [
    provider('anthropic', { configured: true, key_hint: 'configurata, finisce con …abcd', enabled: true }),
    provider('openai'),
    provider('google'),
  ],
  tasks: [task('card'), task('team_proposal')],
  prices: [],
  spend: { month: '2026-10', rows: [] },
  known_models: { anthropic: ['claude-opus-5'], openai: [], google: [] },
}

function mount(config: unknown = CONFIG, overrides: Record<string, () => Response> = {}) {
  const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const path = new URL(String(input), 'http://localhost').pathname
    if (overrides[path]) return overrides[path]()
    if (path === '/api/hub/admin/ai') return answer(200, config)
    return answer(404, { detail: 'not found' })
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AdminModelliAi />
    </QueryClientProvider>,
  )
  return spy
}

afterEach(() => vi.restoreAllMocks())

describe('the Modelli AI page (REB-833)', () => {
  it('clears the key input after saving and never renders the key', async () => {
    mount(CONFIG, {
      '/api/hub/admin/ai/providers/openai/key': () =>
        answer(200, provider('openai', { configured: true, key_hint: 'configurata, finisce con …abcd' })),
    })
    const input = await screen.findByLabelText('Chiave OpenAI')
    const user = userEvent.setup()
    await user.type(input, SECRET)
    await user.click(screen.getAllByRole('button', { name: 'Salva chiave' })[1]!)
    await waitFor(() => expect(input).toHaveValue(''))
    expect(screen.queryByText(new RegExp(SECRET))).toBeNull()
    expect(document.body.innerHTML).not.toContain(SECRET)
  })

  it('shows the hint for a configured provider', async () => {
    mount()
    expect(await screen.findByText(/configurata, finisce con …abcd/)).toBeInTheDocument()
    expect(screen.getAllByText(/non configurata/)).toHaveLength(2)
  })

  it('disables the key inputs with the sentence when the secrets key is missing', async () => {
    mount({ ...CONFIG, secrets_key_present: false })
    expect(await screen.findByText(/Manca REBASE_SECRETS_KEY/)).toBeInTheDocument()
    expect(screen.getByLabelText('Chiave Anthropic')).toBeDisabled()
    expect(screen.getByLabelText('Chiave OpenAI')).toBeDisabled()
  })

  it('shows the answer, latency and cost of «Prova»', async () => {
    mount(CONFIG, {
      '/api/hub/admin/ai/tasks/card/prova': () =>
        answer(200, { answer: 'ok', latency_ms: 812, cost_eur: '0.000123', error: null }),
    })
    await screen.findByLabelText('Chiave Anthropic')
    await userEvent.setup().click(screen.getAllByRole('button', { name: 'Prova' })[0]!)
    expect(await screen.findByText(/812 ms/)).toBeInTheDocument()
    expect(screen.getByText('ok')).toBeInTheDocument()
  })

  it('shows why a task cannot run', async () => {
    mount({
      ...CONFIG,
      tasks: [task('card', { cap_reached: true, reason: 'Tetto giornaliero raggiunto' }), task('team_proposal')],
    })
    expect(await screen.findByText('Tetto giornaliero raggiunto')).toBeInTheDocument()
  })

  it('has the Modelli AI link in the admin sidebar', async () => {
    const { RouterProvider, createMemoryHistory, createRootRoute, createRouter } = await import(
      '@tanstack/react-router'
    )
    const root = createRootRoute({ component: () => <QueryClientProvider client={new QueryClient()}><SidebarNav me={ME} isAdmin /></QueryClientProvider> })
    const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/admin/agents'] }) })
    render(<RouterProvider router={router} />)
    const link = await screen.findByRole('link', { name: 'Modelli AI' })
    expect(link).toHaveAttribute('href', '/admin/modelli-ai')
  })
})
