import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from '@/lib/auth'
import { api } from '@/lib/api'
import { useUpdateUser } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, GET: vi.fn(), PATCH: vi.fn() } }
})

vi.mock('@rebase/analytics/browser', () => ({
  capture: vi.fn(),
  identifyGroup: vi.fn(),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

const FOUNDER = { id: 'u1', email: 'a@p.it', name: 'rebase', role: 'admin', active: true }

/** Shows the name the shell would show, and renames the signed-in user through the hook. */
function Probe() {
  const { user } = useAuth()
  const update = useUpdateUser()
  return (
    <div>
      <p>{user?.name ?? 'none'}</p>
      <button onClick={() => update.mutate({ userId: FOUNDER.id, body: { name: 'Ivan Sala' } })}>
        Rinomina
      </button>
    </div>
  )
}

describe('useUpdateUser (REB-796)', () => {
  beforeEach(() => {
    vi.mocked(api.GET).mockReset()
    vi.mocked(api.PATCH).mockReset()
  })

  it('renames the signed-in user in what useAuth reads, without a reload', async () => {
    let name = FOUNDER.name
    vi.mocked(api.GET).mockImplementation(() => Promise.resolve(ok({ ...FOUNDER, name: name })))
    vi.mocked(api.PATCH).mockImplementation(() => {
      name = 'Ivan Sala'
      return Promise.resolve(ok({ ...FOUNDER, name: name }))
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>,
    )
    expect(await screen.findByText('rebase')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Rinomina' }))

    await waitFor(() => expect(screen.getByText('Ivan Sala')).toBeInTheDocument())
    expect(screen.queryByText('rebase')).not.toBeInTheDocument()
  })
})
