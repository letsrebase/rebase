import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { TaskPage } from './TaskPage'
import type { View } from './view'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

const ME = '33333333-3333-7333-8333-333333333333'

vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useIsAdmin: () => true,
  useAuth: () => ({ user: { id: ME, name: 'Giulia Demo', role: 'admin' }, isLoading: false }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/x">{children}</a>,
}))

function row(over: Record<string, unknown>) {
  return {
    id: 'a',
    title: 'x',
    note: null,
    due_date: null,
    status: 'open',
    completed_at: null,
    assigned_to: ME,
    customer_id: null,
    person_id: null,
    deal_id: null,
    invoice_id: null,
    origin: 'manual',
    rule: null,
    custom_fields: {},
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  }
}

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const group = (items: unknown[]) => ({ total: items.length, items })

const FOUND = row({ id: 'found', title: 'Da cercare', status: 'completed', assigned_to: null })

/** The server for both views and for the one task `?open=` names. */
function serve() {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/activities/today') {
      return ok({
        today: '2026-10-26',
        overdue: group([row({ id: 'late', title: 'Sollecitare Rossi', due_date: '2026-10-25' })]),
        today_due: group([]),
        next_seven_days: group([]),
        without_due_date: group([]),
      })
    }
    if (path === '/api/activities') {
      return ok({ items: [row({ id: 'listed', title: 'Preparare le proposte' })], next_cursor: null })
    }
    if (path === '/api/activities/{task_id}') return ok(FOUND)
    if (path === '/api/users') return ok([{ id: ME, name: 'Giulia Demo', active: true }])
    throw new Error(`unexpected GET ${path}`)
  }) as never)
}

function renderPage(
  view: View,
  extra: { openId?: string; onDismissOpen?: () => void; onViewChange?: (next: View) => void } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <TaskPage view={view} onViewChange={extra.onViewChange ?? vi.fn()} {...extra} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  serve()
})

describe('TaskPage', () => {
  it('opens on Oggi: one title, two tabs, the sections and no table', async () => {
    renderPage('today')

    expect(screen.getByRole('heading', { level: 1, name: 'Attività' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Oggi' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Tutte' })).toHaveAttribute('aria-selected', 'false')
    expect(await screen.findByText('Sollecitare Rossi')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /In ritardo/ })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows today\'s table on Tutte, under the same title', async () => {
    renderPage('all')

    expect(screen.getByRole('heading', { level: 1, name: 'Attività' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Tutte' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('Preparare le proposte')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /In ritardo/ })).not.toBeInTheDocument()
  })

  it('keeps the person chosen on one view when the other is opened', async () => {
    const onViewChange = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    function Page() {
      const [view, setView] = useState<View>('today')
      return <TaskPage view={view} onViewChange={(next) => { onViewChange(next); setView(next) }} />
    }
    render(
      <QueryClientProvider client={client}>
        <Page />
      </QueryClientProvider>,
    )
    await screen.findByText('Sollecitare Rossi')
    await userEvent.click(screen.getByRole('combobox', { name: 'Filtra per assegnatario' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Di chiunque' }))

    await userEvent.click(screen.getByRole('tab', { name: 'Tutte' }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Filtra per assegnatario' })).toHaveTextContent(
      'Di chiunque',
    )
  })

  it('hands the chosen tab back for the route to put in the URL', async () => {
    const onViewChange = vi.fn()
    renderPage('today', { onViewChange })

    await userEvent.click(screen.getByRole('tab', { name: 'Tutte' }))
    expect(onViewChange).toHaveBeenCalledWith('all')
  })

  it.each(['today', 'all'] as const)(
    'opens the attività a link names in its dialog over the %s view, even when no filter shows it',
    async (view) => {
      const onDismissOpen = vi.fn()
      renderPage(view, { openId: 'found', onDismissOpen })

      const dialog = await screen.findByRole('dialog', { name: 'Modifica attività' })
      expect(within(dialog).getByLabelText('Titolo')).toHaveValue('Da cercare')
      // Closed, so the dialog offers the way back to open.
      expect(within(dialog).getByRole('button', { name: 'Riapri' })).toBeInTheDocument()

      // Closing asks the route to take `?open=` off the URL, so picking the same hit
      // again is a new navigation and not a no-op.
      await userEvent.click(within(dialog).getByRole('button', { name: 'Annulla' }))
      await waitFor(() => expect(onDismissOpen).toHaveBeenCalled())
    },
  )

  it('says so when the attività a link names cannot be read', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/activities/{task_id}') {
        return Promise.resolve({
          error: { detail: 'Attività non trovata.' },
          response: new Response(null, { status: 404 }),
        }) as never
      }
      return serveFallback(path) as never
    }) as never)
    renderPage('all', { openId: 'missing' })

    expect(await screen.findByRole('alert')).toHaveTextContent('Attività non trovata.')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

/** The rest of the server, for the test above that overrides one path. */
function serveFallback(path: string): unknown {
  if (path === '/api/activities') return ok({ items: [], next_cursor: null })
  if (path === '/api/users') return ok([])
  throw new Error(`unexpected GET ${path}`)
}
