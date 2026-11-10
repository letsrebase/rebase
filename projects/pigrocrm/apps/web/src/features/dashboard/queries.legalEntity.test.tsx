import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { useCeilingHeadroom, useFiscalEstimate, useYearOverview } from './queries'

/**
 * On a space with two legal entities `GET /api/analytics/year`, `/ceilings` and `/fiscal` refuse
 * a request that names none (422, the space has more than one legal entity). Until the list
 * of legal entities has answered `several` is false on every space, so the panels that follow
 * the sidebar asked without one in that first moment and took the 422, though the page then
 * drew the per-legal-entity blocks right. Nothing may be asked without a legal entity before
 * the list is in; the same request is the right one once it is, on a space with one.
 */
const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

const SPACE: LegalEntityValue = {
  legal_entities: [],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  loaded: false,
  byId: () => undefined,
}

function wrapperFor(value: LegalEntityValue) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <LegalEntityContext value={value}>{children}</LegalEntityContext>
      </QueryClientProvider>
    )
  }
}

const HOOKS = {
  year: { path: '/api/analytics/year', use: (id?: string | null) => useYearOverview(2026, id) },
  ceilings: {
    path: '/api/analytics/ceilings',
    use: (id?: string | null) => useCeilingHeadroom(2026, id),
  },
  fiscal: { path: '/api/analytics/fiscal', use: (id?: string | null) => useFiscalEstimate(2026, id) },
}

afterEach(() => mockGet.mockReset())

describe.each(Object.entries(HOOKS))('%s', (_name, { path, use }) => {
  it('sends nothing without an azienda while the list of aziende has not answered', () => {
    mockGet.mockReturnValue(ok({}))
    renderHook(() => use(), { wrapper: wrapperFor({ ...SPACE, loaded: false }) })
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('asks for an azienda by name at once, list or no list', async () => {
    mockGet.mockReturnValue(ok({}))
    renderHook(() => use('a2'), { wrapper: wrapperFor({ ...SPACE, loaded: false }) })
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith(path, { params: { query: { year: 2026, legal_entity_id: 'a2' } } }),
    )
  })

  it('asks the sidebar’s azienda once the list is in, and asks for none on a one-azienda space', async () => {
    mockGet.mockReturnValue(ok({}))
    renderHook(() => use(), { wrapper: wrapperFor({ ...SPACE, loaded: true }) })
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(path, { params: { query: { year: 2026 } } }))
  })
})
