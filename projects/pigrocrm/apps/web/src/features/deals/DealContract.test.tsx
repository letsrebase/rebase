import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import type { ModuleKey } from '@/lib/modules'
import { seedModules } from '@/lib/modules.testing'
import { ConvertDealButton, DealContractSection } from './DealContract'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    Link: ({ children, params }: { children: React.ReactNode; params: Record<string, string> }) => (
      <a href={`/${Object.values(params).join('/')}`}>{children}</a>
    ),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

const STAGES = [
  { id: 's-open', name: 'Trattativa', type: 'open', position: 1 },
  { id: 's-won', name: 'Vinto', type: 'won', position: 2 },
]

function renderButton(stageId: string, contractId: string | null, modulesOff?: readonly ModuleKey[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (modulesOff !== undefined) seedModules(client, modulesOff)
  return render(
    <QueryClientProvider client={client}>
      <ConvertDealButton deal={{ id: 'd1', pipeline_stage_id: stageId, contract_id: contractId }} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockImplementation((() => ok(STAGES)) as never)
})

describe('ConvertDealButton', () => {
  it('is offered on a won deal that has no contract', async () => {
    renderButton('s-won', null)
    expect(await screen.findByRole('button', { name: 'Crea contratto' })).toBeInTheDocument()
  })

  it('is not offered while the deal is open', async () => {
    renderButton('s-open', null)
    await vi.waitFor(() => expect(api.GET).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: 'Crea contratto' })).not.toBeInTheDocument()
  })

  it('is not offered with contracts off, on a won deal with no contract', async () => {
    renderButton('s-won', null, ['contracts'])
    await vi.waitFor(() => expect(api.GET).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: 'Crea contratto' })).not.toBeInTheDocument()
  })

  it('is not offered once the deal is a contract', async () => {
    renderButton('s-won', 'k1')
    await vi.waitFor(() => expect(api.GET).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: 'Crea contratto' })).not.toBeInTheDocument()
  })
})

function renderSection(contractId: string | null, modulesOff?: readonly ModuleKey[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (modulesOff !== undefined) seedModules(client, modulesOff)
  return render(
    <QueryClientProvider client={client}>
      <DealContractSection contractId={contractId} />
    </QueryClientProvider>,
  )
}

describe('DealContractSection', () => {
  it('links to the contract and says the lines are locked', () => {
    renderSection('k1')
    expect(screen.getByRole('link', { name: 'Apri il contratto' })).toHaveAttribute('href', '/k1')
    expect(screen.getByText(/Le righe di valore sono bloccate/)).toBeInTheDocument()
  })

  it('says nothing on a deal that is not a contract', () => {
    const { container } = renderSection(null)
    expect(container).toBeEmptyDOMElement()
  })

  /** REB-953: a surface of the deals and of the contracts exists only when both are on. */
  it('shows nothing with contracts off', () => {
    const { container } = renderSection('k1', ['contracts'])
    expect(container).toBeEmptyDOMElement()
  })
})
