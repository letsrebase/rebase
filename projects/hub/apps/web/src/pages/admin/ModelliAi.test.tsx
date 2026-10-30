import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { act, render, screen, waitFor, within } from '@testing-library/react'
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
  tasks: [task('card'), task('team_proposal'), task('cv_anonimo')],
  sources: [
    { kind: 'github', enabled: false, updated_at: null, updated_by_name: null },
    { kind: 'links', enabled: false, updated_at: null, updated_by_name: null },
  ],
  prices: [],
  spend: { month: '2026-10', rows: [] },
  known_models: { anthropic: ['claude-opus-5'], openai: [], google: [] },
}

// The «Registro» (REB-861): newest first, a regeneration's start and its end among the changes.
const AUDIT = [
  {
    id: 'e4',
    kind: 'cards_regenerated',
    admin_nome: 'Ivan Sala',
    payload: {
      fonte: 'cli',
      start_action_id: 'e3',
      written: 4,
      failed: 1,
      superseded: 1,
      skipped: 0,
      errored: 0,
      cost_eur: '0.051000',
      failures: [
        { freelancer_id: 'f1', reason: 'source_identifying' },
        { freelancer_id: 'f2', reason: 'superseded' },
      ],
    },
    created_at: '2026-10-30T10:00:00Z',
  },
  {
    id: 'e3',
    kind: 'cards_regenerate',
    admin_nome: 'Ivan Sala',
    payload: { limit: 20, queued: 6, fonte: 'cli', batch: true, batch_id: 'b1' },
    created_at: '2026-10-30T09:00:00Z',
  },
  {
    id: 'e2',
    kind: 'cards_regenerate',
    admin_nome: 'Ivan Sala',
    payload: { limit: 5, queued: 1, fonte: 'web', batch: false },
    created_at: '2026-10-29T09:00:00Z',
  },
  {
    id: 'e1',
    kind: 'ai_task',
    admin_nome: 'Ivan Sala',
    payload: { task: 'card', changed: ['model', 'daily_cap_eur'] },
    created_at: '2026-10-28T09:00:00Z',
  },
  {
    id: 'e0',
    kind: 'ai_switch',
    admin_nome: 'Ivan Sala',
    payload: { source: 'github', changed: ['enabled'], after: { enabled: true } },
    created_at: '2026-10-27T09:00:00Z',
  },
]

const NO_STALE = { count: 0, by_reason: {}, estimated_cost_eur: null, items: [], batch_threshold: 10, batch: null }
const STALE = {
  count: 7,
  by_reason: { prompt: 5, source: 2 },
  estimated_cost_eur: '0.42',
  batch_threshold: 10,
  batch: null,
  items: [
    { freelancer_id: 'f1', nome: 'Ada Lovelace', reason: 'prompt', generated_at: '2026-09-25T10:00:00Z' },
    { freelancer_id: 'f2', nome: 'Grace Hopper', reason: 'source', generated_at: null },
  ],
}

function mount(config: unknown = CONFIG, overrides: Record<string, () => Response> = {}) {
  const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const path = new URL(String(input), 'http://localhost').pathname
    if (overrides[path]) return overrides[path]()
    if (path === '/api/hub/admin/ai') return answer(200, config)
    if (path === '/api/hub/admin/ai/cards/stale') return answer(200, NO_STALE)
    if (path === '/api/hub/admin/ai/audit') return answer(200, [])
    return answer(404, { detail: 'not found' })
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => root, path: '/', component: AdminModelliAi })
  const card = createRoute({ getParentRoute: () => root, path: '/admin/freelance/$id', component: () => <p>scheda</p> })
  const router = createRouter({ routeTree: root.addChildren([page, card]), history: createMemoryHistory() })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
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

  it('names the anonymous CV task in the page’s words', async () => {
    mount()
    expect(await screen.findByText('CV anonimo')).toBeInTheDocument()
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

  it('has the source switches off by default and toggles one through the API', async () => {
    let github = false
    const spy = mount(CONFIG, {
      '/api/hub/admin/ai': () =>
        answer(200, {
          ...CONFIG,
          sources: [
            { kind: 'github', enabled: github, updated_at: null, updated_by_name: null },
            CONFIG.sources[1],
          ],
        }),
      '/api/hub/admin/ai/sources/github/enabled': () => {
        github = true
        return answer(200, { kind: 'github', enabled: true, updated_at: '2026-10-27T09:00:00Z', updated_by_name: 'Ivan' })
      },
    })
    const gh = await screen.findByRole('switch', { name: 'GitHub' })
    expect(gh).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('switch', { name: 'Altri link' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getAllByText('Spenta finché la privacy non è aggiornata')).toHaveLength(2)
    await userEvent.setup().click(gh)
    await waitFor(() => expect(screen.getByRole('switch', { name: 'GitHub' })).toHaveAttribute('aria-checked', 'true'))
    expect(screen.getAllByText('Spenta finché la privacy non è aggiornata')).toHaveLength(1)
    const call = spy.mock.calls.find(([url]) => String(url).endsWith('/sources/github/enabled'))!
    expect(call[1]?.method).toBe('PUT')
    expect(JSON.parse(call[1]?.body as string)).toEqual({ enabled: true })
    // The cards that read a source turn stale when it is switched: the list is read again.
    const staleReads = () => spy.mock.calls.filter(([url]) => String(url).endsWith('/cards/stale')).length
    await waitFor(() => expect(staleReads()).toBeGreaterThan(1))
  })

  it('says a regeneration is already running when nothing was queued', async () => {
    mount(CONFIG, {
      '/api/hub/admin/ai/cards/stale': () => answer(200, STALE),
      '/api/hub/admin/ai/cards/regenerate-stale': () => answer(200, { queued: 0 }),
    })
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    await userEvent.setup().click(await section.findByRole('button', { name: 'Avvia rigenerazione' }))
    expect(await section.findByText(/Una rigenerazione è già in corso/)).toBeInTheDocument()
    expect(section.queryByText(/0 schede messe/)).toBeNull()
  })

  it('shows the stale cards by reason with the estimate and sends the limit on «Avvia rigenerazione»', async () => {
    let stale: unknown = STALE
    const spy = mount(CONFIG, {
      '/api/hub/admin/ai/cards/stale': () => answer(200, stale),
      '/api/hub/admin/ai/cards/regenerate-stale': () => {
        stale = NO_STALE
        return answer(200, { queued: 3 })
      },
    })
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    expect(await section.findByText(/5 per versione del prompt, 2 per fonti cambiate/)).toBeInTheDocument()
    expect(section.getByText(/0,42/)).toBeInTheDocument()
    expect(section.getByRole('link', { name: 'Ada Lovelace' })).toHaveAttribute('href', '/admin/freelance/f1')
    const limit = section.getByLabelText('Quante')
    expect(limit).toHaveValue(5)
    const user = userEvent.setup()
    await user.clear(limit)
    await user.type(limit, '3')
    await user.click(section.getByRole('button', { name: 'Avvia rigenerazione' }))
    expect(await section.findByText(/3 schede messe in coda/)).toBeInTheDocument()
    const call = spy.mock.calls.find(([url]) => String(url).endsWith('/cards/regenerate-stale'))!
    expect(JSON.parse(call[1]?.body as string)).toEqual({ limit: 3 })
    await waitFor(() => expect(section.queryByRole('button', { name: 'Avvia rigenerazione' })).toBeNull())
    expect(section.getByText('Nessuna scheda da aggiornare')).toBeInTheDocument()
  })

  it('says the estimate is not available when no cost is recorded, and refuses a limit above 50', async () => {
    mount(CONFIG, {
      '/api/hub/admin/ai/cards/stale': () => answer(200, { ...STALE, estimated_cost_eur: null }),
    })
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    expect(await section.findByText(/stima non disponibile/)).toBeInTheDocument()
    const user = userEvent.setup()
    await user.clear(section.getByLabelText('Quante'))
    await user.type(section.getByLabelText('Quante'), '51')
    expect(section.getByRole('button', { name: 'Avvia rigenerazione' })).toBeDisabled()
  })

  it('says from how many cards a start goes through the Batch API', async () => {
    mount(CONFIG, { '/api/hub/admin/ai/cards/stale': () => answer(200, STALE) })
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    expect(await section.findByText(/Da 10 schede in su la rigenerazione passa dal Batch API/)).toBeInTheDocument()
  })

  it('shows the running batch with its count, when it was sent and how many are applied, and offers no start', async () => {
    mount(CONFIG, {
      '/api/hub/admin/ai/cards/stale': () =>
        answer(200, {
          ...STALE,
          batch: { status: 'in_progress', count: 14, done: 3, submitted_at: '2026-10-28T09:00:00Z' },
        }),
    })
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    const status = await section.findByRole('status')
    expect(status).toHaveTextContent(/Rigenerazione in corso col Batch API: 14 schede inviate il/)
    expect(status).toHaveTextContent(/3 applicate/)
    expect(section.queryByRole('button', { name: 'Avvia rigenerazione' })).toBeNull()
    expect(section.queryByLabelText('Quante')).toBeNull()
  })

  it('keeps reading after a start that queued cards, so a batch written afterwards is shown', async () => {
    let stale: unknown = STALE
    mount(CONFIG, {
      '/api/hub/admin/ai/cards/stale': () => answer(200, stale),
      '/api/hub/admin/ai/cards/regenerate-stale': () => {
        // The batch's row is written after the response: the first read still misses it.
        setTimeout(() => {
          stale = {
            ...STALE,
            batch: { status: 'in_progress', count: 7, done: 0, submitted_at: '2026-10-28T09:00:00Z' },
          }
        }, 500)
        return answer(200, { queued: 7 })
      },
    })
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    await userEvent.setup().click(await section.findByRole('button', { name: 'Avvia rigenerazione' }))
    expect(await section.findByText(/7 schede messe in coda/)).toBeInTheDocument()
    expect(await section.findByText(/Rigenerazione in corso col Batch API: 7 schede/, {}, { timeout: 8000 })).toBeInTheDocument()
  }, 15000)

  it('says a batch is being sent before it has a time', async () => {
    mount(CONFIG, {
      '/api/hub/admin/ai/cards/stale': () =>
        answer(200, { ...STALE, batch: { status: 'submitting', count: 1, done: 0, submitted_at: null } }),
    })
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    expect(await section.findByRole('status')).toHaveTextContent('1 scheda in invio, 0 applicate.')
  })

  it('says there is nothing to update and offers no button', async () => {
    mount()
    const section = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
    expect(await section.findByText('Nessuna scheda da aggiornare')).toBeInTheDocument()
    expect(section.queryByRole('button')).toBeNull()
  })

  it('says there is nothing in the Registro yet', async () => {
    mount()
    const section = within(await screen.findByRole('region', { name: 'Registro' }))
    expect(await section.findByText('Nessuna modifica registrata.')).toBeInTheDocument()
  })

  it('lists the configuration changes and every regeneration, start and end, with who and the cost (REB-861)', async () => {
    mount(CONFIG, { '/api/hub/admin/ai/audit': () => answer(200, AUDIT) })
    const section = within(await screen.findByRole('region', { name: 'Registro' }))
    const entries = await section.findAllByRole('listitem')
    expect(entries).toHaveLength(5)
    expect(entries[0]).toHaveTextContent(
      'Rigenerazione conclusa: 4 scritte, 2 non riuscite (1 cita un account, un sito o un datore di lavoro, 1 CV o dati cambiati nel frattempo), costo 0,051',
    )
    expect(entries[0]).toHaveTextContent('Ivan Sala')
    expect(entries[1]).toHaveTextContent('Rigenerazione avviata: 6 schede, da riga di comando, Batch API')
    expect(entries[2]).toHaveTextContent('Rigenerazione avviata: 1 scheda, dal sito')
    expect(entries[2]).not.toHaveTextContent('Batch API')
    expect(entries[3]).toHaveTextContent('Compito «Scheda anonima» modificato: modello, tetto giornaliero')
    expect(entries[4]).toHaveTextContent('Fonte «GitHub» accesa')
  })

  it('says a regeneration stopped, why when it is known, and how many cards were never started (REB-861)', async () => {
    const ended = (id: string, extra: object) => ({
      id,
      kind: 'cards_regenerated',
      admin_nome: 'Ivan Sala',
      payload: { fonte: 'web', written: 1, failed: 0, skipped: 0, failures: [], cost_eur: '0.01', stopped: true, ...extra },
      created_at: '2026-10-30T10:00:00Z',
    })
    mount(CONFIG, {
      '/api/hub/admin/ai/audit': () =>
        answer(200, [ended('s2', { left: 4, stop_reason: 'cap_reached' }), ended('s1', { left: 0 })]),
    })
    const section = within(await screen.findByRole('region', { name: 'Registro' }))
    const [capped, unknown] = await section.findAllByRole('listitem')
    expect(capped).toHaveTextContent('interrotta: tetto giornaliero raggiunto, 4 non avviate')
    expect(unknown).toHaveTextContent('interrotta, costo')
    expect(unknown).not.toHaveTextContent('modello non disponibile')
    expect(unknown).not.toHaveTextContent('non avviat')
  })

  it('looks for the start of a regeneration it just asked for until it is signed, however long the cards take to prepare (REB-861)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let audits = 0
      mount(CONFIG, {
        '/api/hub/admin/ai/cards/stale': () => answer(200, STALE),
        '/api/hub/admin/ai/cards/regenerate-stale': () => answer(200, { queued: 6 }),
        '/api/hub/admin/ai/audit': () => {
          audits += 1
          // The start is written after the cards are prepared: not there for the first reads.
          return answer(200, audits > 4 ? [{ ...AUDIT[1], id: 'new', created_at: new Date().toISOString() }] : [])
        },
      })
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const stale = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
      await user.click(await stale.findByRole('button', { name: 'Avvia rigenerazione' }))
      const registro = within(screen.getByRole('region', { name: 'Registro' }))
      expect(registro.queryByText(/Rigenerazione avviata/)).toBeNull()
      for (let waited = 0; waited < 30_000 && !registro.queryByText(/Rigenerazione avviata/); waited += 3_000) {
        await act(() => vi.advanceTimersByTimeAsync(3_000))
      }
      expect(await registro.findByText(/Rigenerazione avviata: 6 schede/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps looking for its own start when another run starts and ends first (REB-861)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const at = new Date().toISOString()
      const otherStart = {
        id: 'other',
        kind: 'cards_regenerate',
        admin_nome: 'Ivan Sala',
        payload: { limit: 1, queued: 1, fonte: 'cli', batch: false },
        created_at: at,
      }
      const otherEnd = {
        id: 'other-end',
        kind: 'cards_regenerated',
        admin_nome: 'Ivan Sala',
        payload: { fonte: 'cli', start_action_id: 'other', written: 1, failed: 0, skipped: 0, failures: [], cost_eur: '0.01' },
        created_at: at,
      }
      let audits = 0
      mount(CONFIG, {
        '/api/hub/admin/ai/cards/stale': () => answer(200, STALE),
        '/api/hub/admin/ai/cards/regenerate-stale': () => answer(200, { queued: 6 }),
        '/api/hub/admin/ai/audit': () => {
          audits += 1
          if (audits <= 2) return answer(200, [])
          // Another run is signed and ends while this page's cards are still being prepared.
          if (audits <= 5) return answer(200, [otherEnd, otherStart])
          return answer(200, [{ ...AUDIT[1], id: 'mine', created_at: at }, otherEnd, otherStart])
        },
      })
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const stale = within(await screen.findByRole('region', { name: 'Schede da aggiornare' }))
      await user.click(await stale.findByRole('button', { name: 'Avvia rigenerazione' }))
      const registro = within(screen.getByRole('region', { name: 'Registro' }))
      for (let waited = 0; waited < 45_000 && !registro.queryByText(/Rigenerazione avviata: 6 schede/); waited += 3_000) {
        await act(() => vi.advanceTimersByTimeAsync(3_000))
      }
      expect(await registro.findByText(/Rigenerazione avviata: 6 schede/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('says the outcome cannot be rebuilt when the start was lost, with what is known and no zeros (REB-861)', async () => {
    const lost = (id: string, extra: object) => ({
      id,
      kind: 'cards_regenerated',
      admin_nome: 'Ivan Sala',
      payload: {
        fonte: 'cli',
        start_action_id: null,
        start_missing: true,
        written: null,
        failed: null,
        superseded: null,
        skipped: null,
        errored: null,
        cost_eur: null,
        failures: null,
        ...extra,
      },
      created_at: '2026-10-30T10:00:00Z',
    })
    mount(CONFIG, {
      '/api/hub/admin/ai/audit': () => answer(200, [lost('l2', { written: 2, failed: 1 }), lost('l1', {})]),
    })
    const section = within(await screen.findByRole('region', { name: 'Registro' }))
    const [known, nothing] = await section.findAllByRole('listitem')
    expect(known).toHaveTextContent('Rigenerazione conclusa: esito non ricostruibile (2 scritte, 1 non riuscita)')
    expect(nothing).toHaveTextContent('Rigenerazione conclusa: esito non ricostruibile')
    expect(nothing).not.toHaveTextContent('scritte')
    expect(nothing).not.toHaveTextContent('costo')
  })
})
