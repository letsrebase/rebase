import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FreelancerEngagements } from './FreelancerEngagements'
import { api } from '@/lib/api'

vi.mock('@/lib/auth', () => ({ useIsAdmin: () => true }))
vi.mock('@/features/engagements/AvailabilityStrip', () => ({
  FreelancerAvailability: ({ freelancerId }: { freelancerId: string }) => (
    <div>Disponibilità di {freelancerId}</div>
  ),
}))
vi.mock('@/features/engagements/EngagementsTable', () => ({
  EngagementsTable: ({ engagements, showFreelancer }: { engagements: { id: string }[]; showFreelancer?: boolean }) => (
    <ul data-show-freelancer={String(showFreelancer)}>
      {engagements.map((e) => (
        <li key={e.id}>{e.id}</li>
      ))}
    </ul>
  ),
}))
vi.mock('@/features/engagements/EngagementFormDialog', () => ({
  EngagementFormDialog: ({ open, freelancerId }: { open: boolean; freelancerId: string }) =>
    open ? <div role="dialog">Nuovo per {freelancerId}</div> : null,
}))

const mockGet = vi.spyOn(api, 'GET')
const ok = (data: unknown) => Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
const failed = () =>
  Promise.resolve({ error: { detail: 'Non risponde' }, response: new Response(null, { status: 500 }) }) as never

beforeEach(() => mockGet.mockReset())

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FreelancerEngagements freelancerId="f1" />
    </QueryClientProvider>,
  )
}

describe('FreelancerEngagements', () => {
  it('lists the freelancer’s engagements with the freelancer column off, and asks for theirs only', async () => {
    mockGet.mockReturnValue(ok({ items: [{ id: 'e1' }, { id: 'e2' }], next_cursor: null }))
    const { container } = renderIt()
    expect(await screen.findByText('e1')).toBeInTheDocument()
    expect(container.querySelector('ul')).toHaveAttribute('data-show-freelancer', 'false')
    expect(screen.getByText('Disponibilità di f1')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith(
      '/api/engagements',
      expect.objectContaining({
        params: { query: expect.objectContaining({ freelancer_id: 'f1' }) },
      }),
    )
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })

  it('says so when there is none', async () => {
    mockGet.mockReturnValue(ok({ items: [], next_cursor: null }))
    renderIt()
    expect(await screen.findByText('Nessun incarico per questo freelance.')).toBeInTheDocument()
  })

  it('shows the error banner alone, with «Riprova», when the read fails', async () => {
    mockGet.mockReturnValue(failed())
    renderIt()
    expect(await screen.findByRole('alert')).toHaveTextContent('Non risponde')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByText('Nessun incarico per questo freelance.')).toBeNull()
  })

  it('reads one more page on «Mostra altri»', async () => {
    mockGet.mockImplementation(((_path: string, init?: { params?: { query?: { cursor?: string } } }) =>
      init?.params?.query?.cursor
        ? ok({ items: [{ id: 'e2' }], next_cursor: null })
        : ok({ items: [{ id: 'e1' }], next_cursor: 'c2' })) as never)
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Mostra altri' }))
    expect(await screen.findByText('e2')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })

  it('opens the form with the freelancer locked on «Nuovo incarico»', async () => {
    mockGet.mockReturnValue(ok({ items: [], next_cursor: null }))
    renderIt()
    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo incarico' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Nuovo per f1')
  })
})
