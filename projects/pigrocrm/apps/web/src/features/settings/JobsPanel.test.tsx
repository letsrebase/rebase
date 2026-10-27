/**
 * Esecuzioni (REB-764): the four cron commands and how each last went. The page decides
 * nothing: it reads `/api/scheduled-jobs` and says what the API says, so what these tests
 * hold is that every state is worded, that the failure is read in words and that an error
 * is never an empty list.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobsPanel } from './JobsPanel'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

const BASE = {
  last_failure_at: null,
  failure: null,
}

const JOBS = [
  {
    ...BASE,
    key: 'gmail-sync',
    label: 'Sincronizzazione Gmail',
    command: 'pigrocrm gmail-sync',
    schedule: 'ogni 15 minuti',
    state: 'not_configured',
    last_run_at: null,
    stale_after_minutes: 30,
  },
  {
    ...BASE,
    key: 'reminders',
    label: 'Passaggio dei promemoria',
    command: 'pigrocrm reminders',
    schedule: 'ogni 15 minuti',
    state: 'ok',
    last_run_at: '2026-10-27T08:55:00Z',
    stale_after_minutes: 30,
  },
  {
    ...BASE,
    key: 'automations',
    label: 'Automazioni',
    command: 'pigrocrm automations',
    schedule: 'ogni giorno alle 6',
    state: 'failed',
    last_run_at: null,
    last_failure_at: '2026-10-27T06:00:00Z',
    failure: 'RuntimeError',
    stale_after_minutes: 1560,
  },
  {
    ...BASE,
    key: 'digest',
    label: 'Riepilogo settimanale',
    command: 'pigrocrm digest',
    schedule: 'ogni lunedì alle 8',
    state: 'late',
    last_run_at: '2026-10-12T06:00:00Z',
    stale_after_minutes: 11520,
  },
]

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <JobsPanel />
    </QueryClientProvider>,
  )
}

function card(key: string): HTMLElement {
  return document.querySelector(`[data-job="${key}"]`) as HTMLElement
}

describe('JobsPanel', () => {
  beforeEach(() => {
    // Only the clock the sentences read: timers stay real, so the query settles.
    vi.useFakeTimers({ toFake: ['Date'], now: Date.parse('2026-10-27T09:00:00Z') })
    vi.mocked(api.GET).mockResolvedValue(ok({ jobs: JOBS }))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('lists the four jobs with their command and schedule, in the API’s order', async () => {
    renderPanel()

    expect(await screen.findByRole('heading', { name: 'Sincronizzazione Gmail' })).toBeVisible()
    expect(screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
      'Sincronizzazione Gmail',
      'Passaggio dei promemoria',
      'Automazioni',
      'Riepilogo settimanale',
    ])
    expect(within(card('digest')).getByText('pigrocrm digest')).toBeVisible()
    expect(within(card('digest')).getByText('pigrocrm digest').parentElement).toHaveTextContent(
      'pigrocrm digest · ogni lunedì alle 8',
    )
    expect(api.GET).toHaveBeenCalledWith('/api/scheduled-jobs')
  })

  it('says each state in a word and a sentence of its own', async () => {
    renderPanel()
    await screen.findByRole('heading', { name: 'Riepilogo settimanale' })

    const ok = within(card('reminders'))
    expect(ok.getByText('In regola')).toBeVisible()
    expect(ok.getByText(/Ultimo giro finito il .+, 5 minuti fa\./)).toBeVisible()

    const late = within(card('digest'))
    expect(late.getByText('In ritardo')).toBeVisible()
    expect(late.getByText(/Nessun giro finito da 15 giorni/)).toBeVisible()

    const failed = within(card('automations'))
    expect(failed.getByText('Non riuscita')).toBeVisible()
    expect(failed.getByText(/errore RuntimeError/)).toBeVisible()
    expect(failed.getByText(/Non è ancora finito nessun giro\./)).toBeVisible()

    const none = within(card('gmail-sync'))
    expect(none.getByText('Non configurata')).toBeVisible()
    expect(none.getByText(/Nessuna casella Gmail collegata/)).toBeVisible()
  })

  it('shows a skeleton while it loads and never a list of zero jobs', async () => {
    vi.mocked(api.GET).mockReturnValue(new Promise(() => {}) as never)

    renderPanel()

    expect(screen.getByRole('status', { name: 'Caricamento esecuzioni' })).toBeVisible()
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull()
  })

  it('keeps the jobs it already has, with the banner above them, when a later read fails', async () => {
    vi.mocked(api.GET).mockResolvedValueOnce(ok({ jobs: JOBS }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <JobsPanel />
      </QueryClientProvider>,
    )
    await screen.findByRole('heading', { name: 'Riepilogo settimanale' })
    vi.mocked(api.GET).mockResolvedValue({
      error: { title: 'Errore', detail: 'Non disponibile', code: 'unavailable' },
      response: new Response(null, { status: 503 }),
    } as never)

    await client.refetchQueries()

    expect(await screen.findByRole('alert')).toHaveTextContent('Non disponibile')
    expect(screen.getByRole('heading', { name: 'Riepilogo settimanale' })).toBeVisible()
  })

  it('renders the server’s own refusal and no jobs when the read fails', async () => {
    vi.mocked(api.GET).mockResolvedValue({
      error: { title: 'Permesso negato', detail: 'Serve il ruolo admin', code: 'permission_denied' },
      response: new Response(null, { status: 403 }),
    } as never)

    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('Serve il ruolo admin')
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull()
  })
})
