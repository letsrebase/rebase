import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '@/features/calendar/queries'
import { api } from '@/lib/api'
import type { ModuleKey } from '@/lib/modules'
import { seedModules } from '@/lib/modules.testing'
import { TaskMeta } from './TaskMeta'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
vi.mock('@/lib/auth', () => ({
  useCanWrite: () => true,
  useIsAdmin: () => true,
  useAuth: () => ({ user: { id: 'me', name: 'Lorenzo', role: 'admin' }, isLoading: false }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const DEAL = 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'
const CUSTOMER = 'bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb'

function task(over: Partial<Task>): Task {
  return {
    id: 't',
    title: 'x',
    assigned_to: null,
    customer_id: null,
    person_id: null,
    deal_id: null,
    invoice_id: null,
    ...over,
  } as Task
}

function renderMeta(over: Partial<Task>, off: readonly ModuleKey[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  seedModules(client, off)
  return render(
    <QueryClientProvider client={client}>
      <TaskMeta task={task(over)} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string) =>
    Promise.resolve({
      data: path === '/api/users' ? [] : { name: 'Rossi', legal_name: 'ACME' },
      response: new Response(null, { status: 200 }),
    })) as never)
})

describe('TaskMeta with deals off (REB-953)', () => {
  it('links a task to its deal while deals is on', async () => {
    renderMeta({ deal_id: DEAL }, [])
    expect(await screen.findByRole('link', { name: /Deal/ })).toHaveAttribute('href', '/app/deal/$dealId')
  })

  it('draws no link to a deal, and does not read it, with deals off', async () => {
    const { container } = renderMeta({ deal_id: DEAL }, ['deals'])
    expect(container.textContent).toBe('')
    expect(vi.mocked(api.GET)).not.toHaveBeenCalledWith('/api/deals/{deal_id}', expect.anything())
  })

  it('still links a task to a customer with deals off', async () => {
    renderMeta({ customer_id: CUSTOMER }, ['deals'])
    expect(await screen.findByRole('link', { name: /Cliente/ })).toBeInTheDocument()
  })
})
