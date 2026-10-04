import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { useCustomers } from '@/features/customers/queries'
import { useTimeEntries } from '@/features/time/queries'
import {
  AziendaContext,
  AziendaProvider,
  aziendaKey,
  useAzienda,
  useAziendaScope,
  useAziendeToName,
  type AziendaRecord,
} from './azienda'

const mockAuth = vi.hoisted(() => ({ userId: 'u1' as string }))
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: mockAuth.userId ? { id: mockAuth.userId } : null }),
}))

vi.mock('@/lib/tenant', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/tenant')>()
  return { ...actual, tenantPrefix: '/studio' }
})

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

import { api } from '@/lib/api'

const GET = api.GET as unknown as Mock

const HUMANCRAFT = { id: 'a1', nome: 'humancraft', predefinita: true, attiva: true } as AziendaRecord
const REBASE = { id: 'a2', nome: 'rebase', predefinita: false, attiva: true } as AziendaRecord
const CLOSED = { id: 'a3', nome: 'chiusa', predefinita: false, attiva: false } as AziendaRecord

function ok(data: unknown) {
  return Promise.resolve({ data, response: { status: 200 } })
}

/** `GET /api/aziende` answers `aziende`; every other read answers an empty page. */
function serve(aziende: AziendaRecord[]) {
  GET.mockImplementation((path: string) =>
    path === '/api/aziende' ? ok(aziende) : ok({ items: [], next_cursor: null }),
  )
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <AziendaProvider>{children}</AziendaProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  mockAuth.userId = 'u1'
  GET.mockReset()
  serve([HUMANCRAFT])
})

describe('AziendaProvider', () => {
  it('is a one-azienda space until the second azienda: nothing selected, nothing several', async () => {
    localStorage.setItem(aziendaKey('u1'), 'a1')
    const { result } = renderHook(() => useAzienda(), { wrapper })
    await waitFor(() => expect(result.current.aziende).toHaveLength(1))
    expect(result.current.several).toBe(false)
    expect(result.current.selected).toBeNull()
  })

  it('offers the active aziende from the second on and keeps the choice per space and user', async () => {
    serve([HUMANCRAFT, REBASE, CLOSED])
    const { result } = renderHook(() => useAzienda(), { wrapper })
    await waitFor(() => expect(result.current.several).toBe(true))
    expect(result.current.aziende.map((a) => a.nome)).toEqual(['humancraft', 'rebase'])
    expect(result.current.selected).toBeNull()

    act(() => result.current.select('a2'))
    expect(result.current.selected).toBe('a2')
    expect(localStorage.getItem('pigrocrm.azienda:/studio:u1')).toBe('a2')
    expect(result.current.byId('a2')?.nome).toBe('rebase')

    act(() => result.current.select(null))
    expect(result.current.selected).toBeNull()
    expect(localStorage.getItem('pigrocrm.azienda:/studio:u1')).toBeNull()
  })

  it('reads the stored choice back, and drops one that names no active azienda any more', async () => {
    serve([HUMANCRAFT, REBASE, CLOSED])
    localStorage.setItem(aziendaKey('u1'), 'a2')
    const { result } = renderHook(() => useAzienda(), { wrapper })
    await waitFor(() => expect(result.current.selected).toBe('a2'))

    localStorage.setItem(aziendaKey('u1'), 'a3')
    const stale = renderHook(() => useAzienda(), { wrapper })
    await waitFor(() => expect(stale.result.current.several).toBe(true))
    expect(stale.result.current.selected).toBeNull()
  })

  it('asks for no list without a session', () => {
    mockAuth.userId = ''
    renderHook(() => useAzienda(), { wrapper })
    expect(GET).not.toHaveBeenCalledWith('/api/aziende')
  })
})

describe('useAziendaScope', () => {
  function withSelection(selected: string | null, several = true) {
    const value = {
      aziende: [HUMANCRAFT, REBASE],
      selected,
      select: vi.fn(),
      several,
      byId: () => undefined,
    }
    return ({ children }: { children: ReactNode }) => {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      return (
        <QueryClientProvider client={client}>
          <AziendaContext value={value}>{children}</AziendaContext>
        </QueryClientProvider>
      )
    }
  }

  it('adds the selection to a list with no owner and leaves an owned list alone', () => {
    const { result } = renderHook(
      () => ({
        plain: useAziendaScope({ search: 'acme' }),
        ofCustomer: useAziendaScope({ customer_id: 'c1' }),
        ofDeal: useAziendaScope({ deal_id: 'd1' }),
      }),
      { wrapper: withSelection('a2') },
    )
    expect(result.current.plain).toEqual({ search: 'acme', azienda_id: 'a2' })
    expect(result.current.ofCustomer).toEqual({ customer_id: 'c1' })
    expect(result.current.ofDeal).toEqual({ deal_id: 'd1' })
  })

  it('adds nothing under «Tutte le aziende» and without a provider', () => {
    const under = renderHook(() => useAziendaScope({ search: 'acme' }), {
      wrapper: withSelection(null),
    })
    expect(under.result.current).toEqual({ search: 'acme' })
    const bare = renderHook(() => useAziendaScope({ search: 'acme' }))
    expect(bare.result.current).toEqual({ search: 'acme' })
  })

  it('reaches the request of a list hook, and not of an owned one', async () => {
    renderHook(
      () => {
        useCustomers({ limit: 200 })
        useTimeEntries({ deal_id: 'd1' })
      },
      { wrapper: withSelection('a2') },
    )
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(2))
    const calls = GET.mock.calls as [string, { params: { query: unknown } }][]
    const customers = calls.find(([path]) => path === '/api/customers')
    const hours = calls.find(([path]) => path === '/api/time-entries')
    if (customers === undefined || hours === undefined) throw new Error('una delle due letture manca')
    expect(customers[1].params.query).toMatchObject({ limit: 200, azienda_id: 'a2' })
    expect(hours[1].params.query).not.toHaveProperty('azienda_id')
  })

  it('names the aziende on the rows only under «tutte» from the second azienda on', () => {
    expect(renderHook(() => useAziendeToName(), { wrapper: withSelection(null) }).result.current)
      .toHaveLength(2)
    expect(renderHook(() => useAziendeToName(), { wrapper: withSelection('a2') }).result.current)
      .toBeUndefined()
    expect(
      renderHook(() => useAziendeToName(), { wrapper: withSelection(null, false) }).result.current,
    ).toBeUndefined()
  })
})
