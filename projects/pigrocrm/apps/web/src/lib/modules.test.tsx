import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { moduleAccess, modulesOfPath, useModules } from './modules'
import { spaceModules } from './modules.testing'

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
const GET = vi.mocked(api.GET) as unknown as ReturnType<typeof vi.fn>

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => GET.mockReset())

describe('modulesOfPath', () => {
  it('gives each page of a module to that module, and the core to none', () => {
    expect(modulesOfPath('/app/deal')).toEqual(['deals'])
    expect(modulesOfPath('/app/deal/list')).toEqual(['deals'])
    expect(modulesOfPath('/app/deal/abc')).toEqual(['deals'])
    expect(modulesOfPath('/app/contracts/abc')).toEqual(['contracts'])
    expect(modulesOfPath('/app/days')).toEqual(['contracts'])
    expect(modulesOfPath('/app/days/new')).toEqual(['contracts'])
    expect(modulesOfPath('/app/approvals/new')).toEqual(['contracts'])
    expect(modulesOfPath('/app/hours')).toEqual(['time'])
    expect(modulesOfPath('/app/taxes')).toEqual(['taxes'])
    expect(modulesOfPath('/app/suppliers')).toEqual(['purchases'])
    expect(modulesOfPath('/app/engagements')).toEqual(['collaborators'])
    expect(modulesOfPath('/app/settings/cost-categories')).toEqual(['purchases'])
    expect(modulesOfPath('/app/settings/rates')).toEqual(['time'])
    expect(modulesOfPath('/app/settings/pipeline')).toEqual(['deals'])
    for (const core of ['/app', '/app/customers', '/app/organizations', '/app/organizations/o1', '/app/invoices', '/app/settings/fiscal']) {
      expect(modulesOfPath(core)).toEqual([])
    }
  })

  it('matches a whole segment, not a prefix of the spelling', () => {
    expect(modulesOfPath('/app/dealer')).toEqual([])
    expect(modulesOfPath('/app/hoursheet')).toEqual([])
  })
})

describe('moduleAccess', () => {
  it('has every module on while the answer is not known', () => {
    const access = moduleAccess(undefined, true)
    expect(access.on('deals', 'contracts', 'time', 'purchases', 'taxes')).toBe(true)
    expect(access.firstOff('deals')).toBeUndefined()
    expect(access.loading).toBe(true)
  })

  it('reads the switches, names the first off and uses the server’s label', () => {
    const data = spaceModules(['purchases'])
    data.modules[3]!.label = 'Acquisti e fatture'
    const access = moduleAccess(data)
    expect(access.on('deals')).toBe(true)
    expect(access.on('deals', 'purchases')).toBe(false)
    expect(access.firstOff('deals', 'purchases', 'taxes')).toBe('purchases')
    expect(access.label('purchases')).toBe('Acquisti e fatture')
    expect(access.loading).toBe(false)
  })
})

describe('useModules', () => {
  it('reads GET /api/settings/modules once and answers from it', async () => {
    GET.mockResolvedValue({ data: spaceModules(['deals']), response: { status: 200 } })
    const { result } = renderHook(() => useModules(), { wrapper })
    expect(result.current.on('deals')).toBe(true)
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.on('deals')).toBe(false))
    // `time` needs `deals`, and the server already reports it off.
    expect(result.current.on('time')).toBe(false)
    expect(result.current.on('contracts', 'purchases', 'taxes')).toBe(true)
    expect(GET).toHaveBeenCalledWith('/api/settings/modules')
  })

  it('keeps every module on when the read fails or answers something else', async () => {
    GET.mockResolvedValue({ data: [], response: { status: 200 } })
    const { result } = renderHook(() => useModules(), { wrapper })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.on('deals', 'contracts', 'time', 'purchases', 'taxes')).toBe(true)
  })
})
