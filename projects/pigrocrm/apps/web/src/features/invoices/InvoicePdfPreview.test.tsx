import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InvoicePdfPreview } from './InvoicePdfPreview'
import type { Invoice } from './queries'
import { fetchWithRefresh } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, fetchWithRefresh: vi.fn() }
})

const ISSUED = {
  id: 'inv-1',
  tipo: 'fattura',
  stato: 'emessa',
  pdf_document_id: 'doc-9',
} as unknown as Invoice
const DRAFT = { id: 'inv-2', tipo: 'fattura', stato: 'bozza', pdf_document_id: null } as unknown as Invoice
const PROFORMA = { id: 'pf-1', tipo: 'proforma', stato: 'confermata', pdf_document_id: null } as unknown as Invoice

function wrap(children: ReactNode, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

function pdfResponse() {
  // A string body: a jsdom `Blob` inside Node's `Response` has no `.stream()`.
  return new Response('%PDF-1.7', { status: 200, headers: { 'Content-Type': 'application/pdf' } })
}

beforeEach(() => {
  vi.mocked(fetchWithRefresh).mockReset()
  // jsdom has neither: the browser turns a Blob into a URL its viewer can open.
  let n = 0
  URL.createObjectURL = vi.fn(() => `blob:pdf-${++n}`)
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('InvoicePdfPreview', () => {
  it('fetches the PDF through the authenticated path and shows it in a frame', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(pdfResponse())
    wrap(<InvoicePdfPreview invoice={ISSUED} />)
    const frame = await screen.findByTitle('Anteprima PDF fattura')
    expect(fetchWithRefresh).toHaveBeenCalledWith('/api/invoices/inv-1/pdf')
    expect(frame).toHaveAttribute('src', expect.stringMatching(/^blob:pdf-1#toolbar=0/))
  })

  it('revokes the blob URL when the page leaves', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(pdfResponse())
    const { unmount } = wrap(<InvoicePdfPreview invoice={ISSUED} />)
    await screen.findByTitle('Anteprima PDF fattura')
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:pdf-1')
  })

  it('makes a fresh URL from the cached bytes on a second mount, never the revoked one', async () => {
    // Back to the page within `staleTime`: the Blob comes from the cache with no
    // request, the URL is a new one, and the revoked one is not the frame's src.
    vi.mocked(fetchWithRefresh).mockResolvedValue(pdfResponse())
    // The app's own `staleTime` (src/lib/query.ts), not the test default of zero.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } })
    const first = wrap(<InvoicePdfPreview invoice={ISSUED} />, client)
    await screen.findByTitle('Anteprima PDF fattura')
    first.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:pdf-1')
    wrap(<InvoicePdfPreview invoice={ISSUED} />, client)
    const frame = await screen.findByTitle('Anteprima PDF fattura')
    expect(frame).toHaveAttribute('src', expect.stringMatching(/^blob:pdf-2#/))
    expect(fetchWithRefresh).toHaveBeenCalledTimes(1)
  })

  it('asks the server for nothing when there is no PDF yet, and says why', async () => {
    wrap(<InvoicePdfPreview invoice={DRAFT} />)
    await waitFor(() => expect(screen.getByText(/si genera all’emissione/)).toBeInTheDocument())
    expect(fetchWithRefresh).not.toHaveBeenCalled()
  })

  it('tells a proforma which button produces its PDF', () => {
    wrap(<InvoicePdfPreview invoice={PROFORMA} />)
    expect(screen.getByText(/«Genera PDF proforma» lo produce/)).toBeInTheDocument()
  })

  it('does not send an imported invoice to a button it does not have', () => {
    const imported = { ...ISSUED, pdf_document_id: null, importata_da: 'esterno' } as Invoice
    wrap(<InvoicePdfPreview invoice={imported} />)
    expect(screen.getByText(/PDF originale non è archiviato/)).toBeInTheDocument()
    expect(fetchWithRefresh).not.toHaveBeenCalled()
  })

  it('shows the server refusal instead of an empty frame', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(JSON.stringify({ code: 'conflict', detail: 'archivio documenti non raggiungibile' }), {
        status: 409,
      }),
    )
    wrap(<InvoicePdfPreview invoice={ISSUED} />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/non raggiungibile/))
    expect(screen.queryByTitle(/Anteprima PDF/)).not.toBeInTheDocument()
  })

  /** REB-168: a 404 is a file that is not there, never the server's log line. */
  it('says in Italian that the PDF is missing when the server answers 404, and where it comes from', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'not_found',
          detail: 'invoice_artifact inv-1#pdf not found',
          entity: 'invoice_artifact',
        }),
        { status: 404 },
      ),
    )
    wrap(<InvoicePdfPreview invoice={ISSUED} />)
    await waitFor(() =>
      expect(
        screen.getByText(
          'Il PDF di questa fattura non è disponibile. «Rigenera documenti» lo genera di nuovo.',
        ),
      ).toBeInTheDocument(),
    )
    expect(screen.queryByText(/invoice_artifact/)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('names no button for a lost proforma PDF, since «Genera PDF proforma» is not on its page', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(JSON.stringify({ code: 'not_found', detail: 'document_blob k not found' }), {
        status: 404,
      }),
    )
    wrap(<InvoicePdfPreview invoice={{ ...PROFORMA, pdf_document_id: 'doc-3' } as Invoice} />)
    await waitFor(() =>
      expect(screen.getByText('Il PDF di questa proforma non è disponibile.')).toBeInTheDocument(),
    )
  })
})
