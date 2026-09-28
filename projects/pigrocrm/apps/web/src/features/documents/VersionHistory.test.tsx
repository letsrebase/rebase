import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { VersionHistory } from './VersionHistory'
import type { DocumentVersion } from './queries'

// REB-294: «Rigenera» is `regenerate_document` (`collaboratore`); the suite drives
// the writer's menu. The readonly shape (no item at all) is its own test below.
vi.mock('@/lib/auth', () => ({ useCanWrite: () => true }))

const VERSIONS: DocumentVersion[] = [
  {
    id: 'v-2',
    document_id: 'doc-1',
    numero: 2,
    template_id: 't-1',
    storage_key: 'acme-0123/doc-1/v2.pdf',
    content_type: 'application/pdf',
    dimensione: 12345,
    hash_sha256: 'a'.repeat(64),
    creato_da: null,
    created_at: '2026-08-10T10:00:00Z',
  },
  {
    id: 'v-1',
    document_id: 'doc-1',
    numero: 1,
    template_id: null,
    storage_key: 'acme-0123/doc-1/v1.pdf',
    content_type: 'application/pdf',
    dimensione: 999,
    hash_sha256: 'b'.repeat(64),
    creato_da: null,
    created_at: '2026-08-09T10:00:00Z',
  },
]

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

// `api.GET`/`api.POST` are spied on directly -- see queries.test.tsx and
// task-14-report.md for why `globalThis.fetch` never intercepts a request made
// through the shared `openapi-fetch` client.
const mockGet = vi.spyOn(api, 'GET')
const mockPost = vi.spyOn(api, 'POST')

afterEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

/** The row's actions live behind the «⋯» menu since the 2026-09-08 revision (design
 *  spec §4); the trigger is labelled per version so a list of them stays unambiguous. */
async function openRowMenu(numero: number) {
  await userEvent.click(await screen.findByRole('button', { name: `Azioni per la versione ${numero}` }))
}

describe('VersionHistory', () => {
  it('lists every version newest first, with its size', async () => {
    mockGet.mockReturnValue(ok(VERSIONS))
    render(<VersionHistory documentId="doc-1" documentTitle="Contratto Rossi" />, { wrapper })
    const items = await screen.findAllByRole('listitem')
    expect(items[0]).toHaveTextContent('v2')
    expect(items[1]).toHaveTextContent('v1')
    expect(items[0]).toHaveTextContent('12,1 kB')
  })

  /**
   * Disabled rather than absent on an uploaded version, which is `RowActions`' own
   * rule: an item that is there on one row and gone on the next is a menu nobody
   * learns. A scan has no template and no variables to rebuild it from, and the server
   * refuses that call by name -- so the item stays, saying so with its own state.
   */
  it('offers regeneration on every row, enabled only where a template backs it', async () => {
    mockGet.mockReturnValue(ok(VERSIONS))
    render(<VersionHistory documentId="doc-1" documentTitle="Contratto Rossi" />, { wrapper })
    await openRowMenu(2)
    expect(screen.getByRole('menuitem', { name: 'Rigenera' })).not.toHaveAttribute('aria-disabled')
    await userEvent.keyboard('{Escape}')

    await openRowMenu(1)
    expect(screen.getByRole('menuitem', { name: 'Rigenera' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('downloads the version behind the row menu', async () => {
    mockGet.mockReturnValue(ok(VERSIONS))
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('%PDF', { status: 200 }))
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:x')
    globalThis.URL.revokeObjectURL = vi.fn()
    render(<VersionHistory documentId="doc-1" documentTitle="Contratto Rossi" />, { wrapper })
    await openRowMenu(2)
    await userEvent.click(screen.getByRole('menuitem', { name: 'Scarica' }))
    await waitFor(() => {
      const called = fetchSpy.mock.calls.some(([url]) => String(url).includes('/download'))
      expect(called).toBe(true)
    })
    fetchSpy.mockRestore()
  })

  it('shows the server error instead of an empty history when the request fails', async () => {
    mockGet.mockReturnValue(failed({ code: 'http_error', detail: 'Non disponibile' }, 503))
    render(<VersionHistory documentId="doc-1" documentTitle="Contratto Rossi" />, { wrapper })
    expect(await screen.findByRole('alert')).toHaveTextContent('Non disponibile')
  })

  /** What Ivan saw for invoices before REB-168, now reproduced for a version (REB-388):
   *  a stored file gone from disk still 404s, and the banner used to show the server's
   *  own log line, `document_blob <key> not found`. It names the file -- the document's
   *  title and the version clicked -- instead, in Italian, never the raw key. */
  it('turns a 404 on download into a sentence naming the file, never the raw identifier', async () => {
    mockGet.mockReturnValue(ok(VERSIONS))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Not Found',
          status: 404,
          code: 'not_found',
          detail: 'document_blob acme-0123/doc-1/v2.pdf not found',
          entity: 'document_blob',
        }),
        { status: 404, headers: { 'content-type': 'application/problem+json' } },
      ),
    )
    render(<VersionHistory documentId="doc-1" documentTitle="Contratto Rossi" />, { wrapper })
    await openRowMenu(2)
    await userEvent.click(screen.getByRole('menuitem', { name: 'Scarica' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('«Contratto Rossi (v2)»')
    expect(alert).not.toHaveTextContent('document_blob')
  })

  it('posts a regeneration for the version it was asked about', async () => {
    mockGet.mockReturnValue(ok(VERSIONS))
    mockPost.mockReturnValue(ok({ ...VERSIONS[0], numero: 3 }))
    render(<VersionHistory documentId="doc-1" documentTitle="Contratto Rossi" />, { wrapper })
    await openRowMenu(2)
    await userEvent.click(screen.getByRole('menuitem', { name: 'Rigenera' }))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/api/documents/{document_id}/versions/{numero}/regenerate',
        expect.objectContaining({ params: { path: { document_id: 'doc-1', numero: 2 } } }),
      )
    })
  })
})
