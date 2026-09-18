/**
 * §9.5's third surface: the two rules, their switch, and the last executions with their
 * outcome.
 *
 * The non-executions matter most, so they are what the assertions are about: without them,
 * "it did not fire" and "it was not supposed to fire" are the same empty list.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AutomationsPanel } from './AutomationsPanel'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const DESCRIPTION = {
  configurazione: {
    a1_offerta_accettata_vince_deal: true,
    a2_offerta_inviata_avanza_deal: false,
  },
  regole: [
    {
      codice: 'A1',
      titolo: 'Offerta accettata → deal vinto',
      descrizione: 'Sposta il deal.',
      attiva: true,
    },
    {
      codice: 'A2',
      titolo: 'Offerta inviata → il deal avanza',
      descrizione: 'Mai indietro.',
      attiva: false,
    },
  ],
  esecuzioni: [
    {
      kind: 'automazione.stage_spostato',
      occurred_at: '2026-03-10T09:00:00Z',
      deal_id: 'd1',
      regola: 'A1',
      motivo: null,
      payload: { a: 'Vinto' },
    },
    {
      kind: 'automazione.non_eseguita',
      occurred_at: '2026-03-09T09:00:00Z',
      deal_id: 'd2',
      regola: 'A1',
      motivo: 'stage_bersaglio_ambiguo',
      payload: {},
    },
  ],
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AutomationsPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PUT).mockReset()
  vi.mocked(api.GET).mockResolvedValue(ok(DESCRIPTION))
  vi.mocked(api.PUT).mockResolvedValue(ok(DESCRIPTION.configurazione))
})

describe('AutomationsPanel', () => {
  it('lists both rules with their switch reflecting the server state', async () => {
    renderPanel()
    const a1 = await screen.findByRole('switch', { name: /offerta accettata/i })
    const a2 = screen.getByRole('switch', { name: /offerta inviata/i })
    expect(a1).toBeChecked()
    // `false` is a value, not a blank: an off switch must render off, not unset.
    expect(a2).not.toBeChecked()
  })

  it('sends only the rule that changed', async () => {
    renderPanel()
    const a1 = await screen.findByRole('switch', { name: /offerta accettata/i })
    await userEvent.click(a1)
    expect(api.PUT).toHaveBeenCalledWith('/api/automation-config', {
      body: { a1_offerta_accettata_vince_deal: false },
    })
  })

  it('sends the other rule on its own, with no field it was not asked to change', async () => {
    // `exclude_unset=True` on the server means an omitted field changes nothing, so
    // sending both would silently overwrite whatever the other one had become since the
    // page loaded. `extra="forbid"` also means a misspelled key is a 422, not a no-op.
    renderPanel()
    await userEvent.click(await screen.findByRole('switch', { name: /offerta inviata/i }))
    expect(api.PUT).toHaveBeenCalledWith('/api/automation-config', {
      body: { a2_offerta_inviata_avanza_deal: true },
    })
  })

  it('shows a non-execution with its reason in plain Italian', async () => {
    renderPanel()
    // The raw enum value would tell the user nothing; the reason is what they act on.
    expect(await screen.findByText(/più di uno stato «vinto»/i)).toBeInTheDocument()
    expect(screen.queryByText(/stage_bersaglio_ambiguo/)).not.toBeInTheDocument()
  })

  it('falls back to the raw reason rather than hiding a run it cannot label', async () => {
    // A reason added to the backend's Literal and not to this map must still be visible:
    // a silently unlabelled non-execution is the failure mode this whole page exists to
    // prevent.
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...DESCRIPTION,
        esecuzioni: [{ ...DESCRIPTION.esecuzioni[1]!, motivo: 'motivo_futuro' }],
      }),
    )
    renderPanel()
    expect(await screen.findByText(/motivo_futuro/)).toBeInTheDocument()
  })

  it('distinguishes an execution from a non-execution', async () => {
    renderPanel()
    expect(await screen.findByText(/spostato/i)).toBeInTheDocument()
    expect(screen.getByText(/non eseguita/i)).toBeInTheDocument()
  })

  it('names a configuration change rather than calling it a move', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...DESCRIPTION,
        esecuzioni: [
          {
            kind: 'automazione.configurazione_modificata',
            occurred_at: '2026-03-11T09:00:00Z',
            deal_id: null,
            regola: null,
            motivo: null,
            payload: {},
          },
        ],
      }),
    )
    renderPanel()
    expect(await screen.findByText(/configurazione modificata/i)).toBeInTheDocument()
  })

  it('says the log is empty rather than showing nothing at all', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ ...DESCRIPTION, esecuzioni: [] }))
    renderPanel()
    expect(await screen.findByText(/nessuna esecuzione registrata/i)).toBeInTheDocument()
  })

  it('renders an error banner rather than an empty rule list on failure', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      failed({ title: 'Errore', detail: 'Non disponibile', code: 'unavailable' }, 500),
    )
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Non disponibile')
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(screen.queryByText(/nessuna esecuzione registrata/i)).not.toBeInTheDocument()
  })

  it('shows the failure of a refused change without erasing the switches', async () => {
    // The stale-banner defect fixed twice in this codebase (CostCategoriesPanel,
    // RatesPanel): the failure is rendered from the mutation's own `error`, never copied
    // into component state where a later success cannot clear it.
    renderPanel()
    const a1 = await screen.findByRole('switch', { name: /offerta accettata/i })
    vi.mocked(api.PUT).mockResolvedValue(
      failed({ title: 'Errore', detail: 'Non permesso', code: 'permission_denied' }, 403),
    )
    await userEvent.click(a1)
    expect(await screen.findByRole('alert')).toHaveTextContent('Non permesso')
    expect(screen.getByRole('switch', { name: /offerta accettata/i })).toBeInTheDocument()
  })
})
