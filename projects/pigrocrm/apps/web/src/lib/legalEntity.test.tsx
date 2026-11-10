import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { useOrganizations } from '@/features/organizations/queries'
import { useTimeEntries } from '@/features/time/queries'
import {
  LegalEntityContext,
  LegalEntityProvider,
  legalEntityKey,
  useLegalEntity,
  useLegalEntityScope,
  useLegalEntitiesToName,
  type LegalEntityRecord,
} from './legalEntity'

const mockAuth = vi.hoisted(() => ({
  userId: 'u1' as string,
  legal_entities: null as string[] | null,
}))
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    user: mockAuth.userId ? { id: mockAuth.userId, legal_entities: mockAuth.legal_entities } : null,
  }),
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

const HUMANCRAFT = { id: 'a1', name: 'humancraft', is_default: true, active: true } as LegalEntityRecord
const REBASE = { id: 'a2', name: 'rebase', is_default: false, active: true } as LegalEntityRecord
const CLOSED = { id: 'a3', name: 'closed', is_default: false, active: false } as LegalEntityRecord

function ok(data: unknown) {
  return Promise.resolve({ data, response: { status: 200 } })
}

/** `GET /api/aziende` answers `legal_entities`; every other read answers an empty page. */
function serve(legal_entities: LegalEntityRecord[]) {
  GET.mockImplementation((path: string) =>
    path === '/api/legal-entities' ? ok(legal_entities) : ok({ items: [], next_cursor: null }),
  )
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <LegalEntityProvider>{children}</LegalEntityProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  mockAuth.userId = 'u1'
  mockAuth.legal_entities = null
  GET.mockReset()
  serve([HUMANCRAFT])
})

describe('LegalEntityProvider', () => {
  it('is a one-legal-entity space until the second azienda: nothing selected, nothing several', async () => {
    localStorage.setItem(legalEntityKey('u1'), 'a1')
    const { result } = renderHook(() => useLegalEntity(), { wrapper })
    await waitFor(() => expect(result.current.legal_entities).toHaveLength(1))
    expect(result.current.several).toBe(false)
    expect(result.current.selected).toBeNull()
  })

  it('offers the active aziende from the second on and keeps the choice per space and user', async () => {
    serve([HUMANCRAFT, REBASE, CLOSED])
    const { result } = renderHook(() => useLegalEntity(), { wrapper })
    await waitFor(() => expect(result.current.several).toBe(true))
    expect(result.current.legal_entities.map((a) => a.name)).toEqual(['humancraft', 'rebase'])
    expect(result.current.selected).toBeNull()

    act(() => result.current.select('a2'))
    expect(result.current.selected).toBe('a2')
    expect(localStorage.getItem('pigrocrm.legal-entity:/studio:u1')).toBe('a2')
    expect(result.current.byId('a2')?.name).toBe('rebase')

    act(() => result.current.select(null))
    expect(result.current.selected).toBeNull()
    expect(localStorage.getItem('pigrocrm.legal-entity:/studio:u1')).toBeNull()
  })

  it('reads the stored choice back, and drops one that names no active azienda any more', async () => {
    serve([HUMANCRAFT, REBASE, CLOSED])
    localStorage.setItem(legalEntityKey('u1'), 'a2')
    const { result } = renderHook(() => useLegalEntity(), { wrapper })
    await waitFor(() => expect(result.current.selected).toBe('a2'))

    localStorage.setItem(legalEntityKey('u1'), 'a3')
    const stale = renderHook(() => useLegalEntity(), { wrapper })
    await waitFor(() => expect(stale.result.current.several).toBe(true))
    expect(stale.result.current.selected).toBeNull()
  })

  it('asks for no list without a session', () => {
    mockAuth.userId = ''
    renderHook(() => useLegalEntity(), { wrapper })
    expect(GET).not.toHaveBeenCalledWith('/api/legal-entities')
  })

  // --- a scoped session (REB-635, spec 2026-10-03 §1.11, §5) ---------------------------

  it('pins the selection for a scoped person with one azienda, whatever was stored', async () => {
    mockAuth.legal_entities = ['a2']
    serve([REBASE]) // what the server answers a person scoped to «rebase»
    localStorage.setItem(legalEntityKey('u1'), 'a1')
    const { result } = renderHook(() => useLegalEntity(), { wrapper })
    await waitFor(() => expect(result.current.legal_entities).toHaveLength(1))
    expect(result.current.scoped).toBe(true)
    expect(result.current.pinned).toBe(true)
    expect(result.current.several).toBe(false)
    expect(result.current.selected).toBe('a2')
    // The pinned selection reaches a list's request like a chosen one would.
    const scope = renderHook(() => useLegalEntityScope({ limit: 50 }), { wrapper })
    await waitFor(() => expect(scope.result.current).toEqual({ limit: 50, legal_entity_id: 'a2' }))
  })

  it('offers a scoped person with several aziende theirs, with nothing pinned', async () => {
    mockAuth.legal_entities = ['a1', 'a2']
    serve([HUMANCRAFT, REBASE])
    const { result } = renderHook(() => useLegalEntity(), { wrapper })
    await waitFor(() => expect(result.current.several).toBe(true))
    expect(result.current.scoped).toBe(true)
    expect(result.current.pinned).toBe(false)
    expect(result.current.selected).toBeNull()
    act(() => result.current.select('a1'))
    expect(result.current.selected).toBe('a1')
  })

  it('is not pinned for an unscoped person with one azienda: nothing is sent, as before', async () => {
    const { result } = renderHook(() => useLegalEntity(), { wrapper })
    await waitFor(() => expect(result.current.legal_entities).toHaveLength(1))
    expect(result.current.scoped).toBe(false)
    expect(result.current.pinned).toBe(false)
    expect(result.current.selected).toBeNull()
  })
})

describe('useLegalEntityScope', () => {
  function withSelection(selected: string | null, several = true) {
    const value = {
      legal_entities: [HUMANCRAFT, REBASE],
      selected,
      select: vi.fn(),
      several,
      scoped: false,
      pinned: false,
      loaded: true,
      byId: () => undefined,
    }
    return ({ children }: { children: ReactNode }) => {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      return (
        <QueryClientProvider client={client}>
          <LegalEntityContext value={value}>{children}</LegalEntityContext>
        </QueryClientProvider>
      )
    }
  }

  it('adds the selection to a list with no owner and leaves an owned list alone', () => {
    const { result } = renderHook(
      () => ({
        plain: useLegalEntityScope({ search: 'acme' }),
        ofOrganization: useLegalEntityScope({ customer_id: 'c1' }),
        ofDeal: useLegalEntityScope({ deal_id: 'd1' }),
      }),
      { wrapper: withSelection('a2') },
    )
    expect(result.current.plain).toEqual({ search: 'acme', legal_entity_id: 'a2' })
    expect(result.current.ofOrganization).toEqual({ customer_id: 'c1' })
    expect(result.current.ofDeal).toEqual({ deal_id: 'd1' })
  })

  it('adds nothing under «Tutte le aziende» and without a provider', () => {
    const under = renderHook(() => useLegalEntityScope({ search: 'acme' }), {
      wrapper: withSelection(null),
    })
    expect(under.result.current).toEqual({ search: 'acme' })
    const bare = renderHook(() => useLegalEntityScope({ search: 'acme' }))
    expect(bare.result.current).toEqual({ search: 'acme' })
  })

  it('reaches the request of a list hook, and not of an owned one', async () => {
    renderHook(
      () => {
        useOrganizations({ limit: 200 })
        useTimeEntries({ deal_id: 'd1' })
      },
      { wrapper: withSelection('a2') },
    )
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(2))
    const calls = GET.mock.calls as [string, { params: { query: unknown } }][]
    const organizations = calls.find(([path]) => path === '/api/organizations')
    const hours = calls.find(([path]) => path === '/api/time-entries')
    if (organizations === undefined || hours === undefined) throw new Error('one of the two reads is missing')
    expect(organizations[1].params.query).toMatchObject({ limit: 200, legal_entity_id: 'a2' })
    expect(hours[1].params.query).not.toHaveProperty('legal_entity_id')
  })

  it('names the aziende on the rows only under «tutte» from the second azienda on', () => {
    expect(renderHook(() => useLegalEntitiesToName(), { wrapper: withSelection(null) }).result.current)
      .toHaveLength(2)
    expect(renderHook(() => useLegalEntitiesToName(), { wrapper: withSelection('a2') }).result.current)
      .toBeUndefined()
    expect(
      renderHook(() => useLegalEntitiesToName(), { wrapper: withSelection(null, false) }).result.current,
    ).toBeUndefined()
  })
})
