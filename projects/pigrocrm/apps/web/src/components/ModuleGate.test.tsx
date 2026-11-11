import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { seedModules } from '@/lib/modules.testing'
import type { ModuleKey } from '@/lib/modules'
import { ModuleGate } from './ModuleGate'

const route = vi.hoisted(() => ({ pathname: '/app/deal' }))
vi.mock('@tanstack/react-router', () => ({ useLocation: () => ({ pathname: route.pathname }) }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
const GET = vi.mocked(api.GET) as unknown as ReturnType<typeof vi.fn>

function renderGate(off: readonly ModuleKey[] | 'unknown') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (off !== 'unknown') seedModules(client, off)
  return render(
    <QueryClientProvider client={client}>
      <ModuleGate>
        <p>La pagina</p>
      </ModuleGate>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  route.pathname = '/app/deal'
  GET.mockReset()
})

describe('ModuleGate (REB-953)', () => {
  it('shows the page of a module that is on', () => {
    renderGate([])
    expect(screen.getByText('La pagina')).toBeInTheDocument()
  })

  it('names the module in the sentence the API’s own 404 carries, in place of the page', () => {
    renderGate(['deals'])
    expect(screen.queryByText('La pagina')).toBeNull()
    expect(
      screen.getByText(
        'Il modulo Deal è spento: un amministratore può accenderlo in Impostazioni → Moduli',
      ),
    ).toBeInTheDocument()
  })

  it.each([
    ['/app/deal/list', ['deals'], 'Deal'],
    ['/app/deal/3f0c2a10', ['deals'], 'Deal'],
    ['/app/contracts/3f0c2a10', ['contracts'], 'Contratti'],
    ['/app/days/new', ['contracts'], 'Contratti'],
    ['/app/approvals/new', ['contracts'], 'Contratti'],
    ['/app/hours', ['time'], 'Ore'],
    ['/app/settings/cost-categories', ['purchases'], 'Acquisti'],
    // «Fornitori» is the purchases module's page; a stale bookmark says why (REB-1053).
    ['/app/suppliers', ['purchases'], 'Acquisti'],
    ['/app/taxes', ['taxes'], 'Tasse'],
  ] as const)('turns %s away when %j is off', (pathname, off, label) => {
    route.pathname = pathname
    renderGate(off)
    expect(screen.queryByText('La pagina')).toBeNull()
    expect(screen.getByText(new RegExp(`Il modulo ${label} è spento`))).toBeInTheDocument()
  })

  it('turns the hours away when deals is what is off: the server reports time off with it', () => {
    route.pathname = '/app/hours'
    renderGate(['deals'])
    expect(screen.getByText(/Il modulo Ore è spento/)).toBeInTheDocument()
    expect(screen.queryByText('La pagina')).toBeNull()
  })

  it('lets the core through whatever is off, «Clienti» and an organization\'s own page included', () => {
    for (const pathname of ['/app/organizations/o1', '/app/customers']) {
      route.pathname = pathname
      const { unmount } = renderGate(['deals', 'contracts', 'time', 'purchases', 'taxes'])
      expect(screen.getByText('La pagina')).toBeInTheDocument()
      unmount()
    }
  })

  it('does not turn a page away for one module off beside another that is on', () => {
    route.pathname = '/app/days'
    renderGate(['deals'])
    expect(screen.getByText('La pagina')).toBeInTheDocument()
  })

  it('waits for the first answer on a guarded path, then decides', async () => {
    GET.mockReturnValue(new Promise(() => {}))
    renderGate('unknown')
    expect(screen.queryByText('La pagina')).toBeNull()
    expect(screen.getByText('Caricamento…')).toBeInTheDocument()
  })

  it('lets a guarded page through when the modules could not be read', async () => {
    GET.mockResolvedValue({ data: [], response: { status: 200 } })
    renderGate('unknown')
    expect(await screen.findByText('La pagina')).toBeInTheDocument()
  })

  it('never waits on a page of the core', () => {
    route.pathname = '/app/organizations'
    GET.mockReturnValue(new Promise(() => {}))
    renderGate('unknown')
    expect(screen.getByText('La pagina')).toBeInTheDocument()
  })
})
