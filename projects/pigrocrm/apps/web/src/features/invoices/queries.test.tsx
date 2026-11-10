import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { downloadInvoiceArtifact, useInvoice, useInvoices, useProduceArtifacts } from './queries'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

// `openapi-fetch`'s client captures `globalThis.fetch` into a closure once, at
// `createClient()` time (see its own source: `fetch: baseFetch = globalThis.fetch`)
// -- so stubbing `globalThis.fetch` from inside a test never intercepts a request
// made through the shared `api` client; it only ever hits the real network. Every
// other query-layer test in this codebase (features/documents/queries.test.tsx,
// features/deals/queries.test.tsx) spies on `api.GET`/`api.PATCH` directly for
// exactly this reason, and this file follows the same convention.
const mockGet = vi.spyOn(api, 'GET')

afterEach(() => {
  mockGet.mockReset()
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

describe('useInvoice', () => {
  it('does not fire a request for an empty id', async () => {
    // B1: `useOrganization('')` once produced a 307 to the *list* endpoint with an
    // absolute URL, bypassing even the Vite proxy. The guard is on the hook, and this
    // is the test the original fix never got.
    const { result } = renderHook(() => useInvoice(''), { wrapper })
    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'))
    expect(result.current.isPending).toBe(true)
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('fires for a real id', async () => {
    mockGet.mockReturnValueOnce(ok({ id: '0192f0aa-0000-7000-8000-000000000001' }))
    const { result } = renderHook(() => useInvoice('0192f0aa-0000-7000-8000-000000000001'), {
      wrapper,
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockGet).toHaveBeenCalledWith(
      '/api/invoices/{invoice_id}',
      expect.objectContaining({
        params: { path: { invoice_id: '0192f0aa-0000-7000-8000-000000000001' } },
      }),
    )
  })
})

describe('useInvoices', () => {
  it('passes the filters through as query parameters', async () => {
    mockGet.mockReturnValueOnce(ok({ items: [], next_cursor: null }))
    const { result } = renderHook(() => useInvoices({ type: 'proforma', year: 2026 }), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockGet).toHaveBeenCalledWith(
      '/api/invoices',
      expect.objectContaining({ params: { query: { type: 'proforma', year: 2026 } } }),
    )
  })
})

/**
 * The one thing about a download `openapi-fetch` cannot do for us is also the one
 * thing that used to go wrong after a quiet quarter of an hour: the access cookie
 * lasts fifteen minutes, and a raw `fetch` that meets a 401 has nowhere to go. These
 * exercise `fetchWithRefresh` (lib/api.ts) *through* the download, because the bug was
 * never in the helper -- it was in this function not having one.
 *
 * `globalThis.fetch` is stubbed here and not, as elsewhere in this file, `api.GET`:
 * a raw `fetch` is looked up at call time, so a stub really does intercept it --
 * unlike `openapi-fetch`'s client, which captured `globalThis.fetch` into a closure
 * at `createClient()` time (see this file's header).
 */
describe('downloadInvoiceArtifact', () => {
  const clicks = vi.fn()

  beforeEach(() => {
    // jsdom implements neither, and both are called on the happy path.
    URL.createObjectURL = vi.fn(() => 'blob:pdf')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(clicks)
    clicks.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function pdf() {
    return new Response('%PDF', { status: 200 })
  }

  function unauthenticated() {
    return new Response(JSON.stringify({ detail: 'Autenticazione richiesta' }), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    })
  }

  it('refreshes the session and retries when the access cookie has expired', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(pdf())
    vi.stubGlobal('fetch', fetchMock)

    await downloadInvoiceArtifact('inv-1', 'pdf')

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/invoices/inv-1/pdf',
      '/api/auth/refresh',
      '/api/invoices/inv-1/pdf',
    ])
    expect(URL.createObjectURL).toHaveBeenCalled()
    expect(clicks).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('surfaces the session as gone when the refresh fails too', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(unauthenticated())
    vi.stubGlobal('fetch', fetchMock)

    // The *original* 401 is what reaches the caller, not the refresh's own: what
    // failed, from the user's point of view, is the download.
    await expect(downloadInvoiceArtifact('inv-1', 'pdf')).rejects.toMatchObject({
      code: 'unauthenticated',
      status: 401,
    })
    expect(clicks).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('does not refresh anything when the first request succeeds', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(pdf())
    vi.stubGlobal('fetch', fetchMock)

    await downloadInvoiceArtifact('inv-1', 'xml')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/invoices/inv-1/xml')
    vi.unstubAllGlobals()
  })
})

describe('useProduceArtifacts', () => {
  it('invalidates the PDF preview, whose key does not change on a new version', async () => {
    // `produce_artifacts` adds a version under the same `pdf_document_id`, so the
    // preview's key is identical before and after: only an invalidation reaches it.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['invoice-pdf', 'inv-1', 'doc-1'], new Blob(['old']))
    const post = vi.spyOn(api, 'POST').mockResolvedValue(ok([]) as never)
    const { result } = renderHook(() => useProduceArtifacts('inv-1'), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    })
    result.current.mutate(undefined)
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(post).toHaveBeenCalled()
    expect(client.getQueryState(['invoice-pdf', 'inv-1', 'doc-1'])?.isInvalidated).toBe(true)
  })
})
