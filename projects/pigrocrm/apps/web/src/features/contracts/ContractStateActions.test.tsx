import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ContractStateActions } from './ContractStateActions'
import type { Contract } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

let canChange = true
vi.mock('@/lib/auth', () => ({ useCan: () => canChange }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function renderActions(stato: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ContractStateActions contract={{ id: 'c1', titolo: 'Sviluppo', stato } as Contract} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.POST).mockImplementation((() => ok({})) as never)
  canChange = true
})

describe('ContractStateActions', () => {
  it('activates a bozza with one press', async () => {
    renderActions('bozza')
    await userEvent.click(screen.getByRole('button', { name: 'Attiva' }))
    expect(api.POST).toHaveBeenCalledWith('/api/contracts/{contract_id}/state', {
      params: { path: { contract_id: 'c1' } },
      body: { stato: 'attivo' },
    })
  })

  it('offers no Attiva on a contract that is not a bozza, only the other states in the menu', async () => {
    renderActions('attivo')
    expect(screen.queryByRole('button', { name: 'Attiva' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Stato' }))
    expect(await screen.findByRole('menuitem', { name: 'Segna come «terminato»' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Segna come «attivo»' })).not.toBeInTheDocument()
  })

  it('asks before ending a contract and does nothing when the answer is no', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderActions('attivo')
    await userEvent.click(screen.getByRole('button', { name: 'Stato' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Segna come «terminato»' }))
    expect(confirm).toHaveBeenCalled()
    expect(api.POST).not.toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('renders nothing for someone who cannot change a state', () => {
    canChange = false
    const { container } = renderActions('bozza')
    expect(container).toBeEmptyDOMElement()
  })
})
