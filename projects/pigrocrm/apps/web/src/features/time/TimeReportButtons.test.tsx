import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { downloadDocument } from '@/features/documents/queries'
import { TimeReportButtons } from './TimeReportButtons'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/features/documents/queries', () => ({ downloadDocument: vi.fn() }))

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

const DOCUMENT = { id: 'doc-9', tipo: 'rapporto_ore', versione_corrente: 1 }

function renderButtons() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TimeReportButtons dealId="d-1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(downloadDocument).mockReset()
  vi.mocked(downloadDocument).mockResolvedValue(undefined)
})

describe('TimeReportButtons', () => {
  /**
   * The XLSX is the one of the two that really is a link. The endpoint streams those
   * bytes with a `Content-Disposition`, so the browser fetches them itself and no
   * JavaScript touches them.
   */
  it('offers the XLSX as a plain anchor', () => {
    renderButtons()
    const link = screen.getByRole('link', { name: /XLSX/ })
    expect(link).toHaveAttribute(
      'href',
      expect.stringContaining('/api/deals/d-1/time-report?mese='),
    )
    expect(link.getAttribute('href')).toContain('formato=xlsx')
  })

  /**
   * The defect this file was written for. The PDF branch of that endpoint archives a
   * document and answers `201 application/json`; when this was an anchor too, clicking
   * «PDF» navigated the browser to a page of JSON. It has to be a request whose answer
   * is read, followed by a download of the archived document -- which is the only path
   * that carries authorisation on either storage backend.
   */
  it('renders the PDF and then downloads the archived document, never linking to JSON', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(DOCUMENT, 201))
    renderButtons()

    expect(screen.queryByRole('link', { name: /^PDF$/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /PDF/ }))

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.GET).mock.calls[0] as unknown as [
      string,
      { params: { query: Record<string, string> } },
    ]
    expect(path).toBe('/api/deals/{deal_id}/time-report')
    expect(options.params.query.formato).toBe('pdf')
    await waitFor(() => expect(downloadDocument).toHaveBeenCalledWith('doc-9'))
  })

  /** The month the user picked is the month that gets rendered -- not today's. */
  it('sends the chosen month', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(DOCUMENT, 201))
    renderButtons()

    const month = screen.getByLabelText('Rapporto ore del mese')
    await userEvent.clear(month)
    await userEvent.type(month, '2026-03')
    await userEvent.click(screen.getByRole('button', { name: /PDF/ }))

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    const [, options] = vi.mocked(api.GET).mock.calls[0] as unknown as [
      string,
      { params: { query: Record<string, string> } },
    ]
    expect(options.params.query.mese).toBe('2026-03')
  })

  /**
   * A month with no hours, or a deal the actor cannot read, is refused by the server.
   * As an anchor that could only ever have surfaced as a browser error page, away from
   * the screen the user was on; as a mutation it has somewhere to say so.
   */
  it('shows a refused render in place, and does not download anything', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      failed({ detail: 'nessuna ora registrata nel periodo' }, 422),
    )
    renderButtons()

    await userEvent.click(screen.getByRole('button', { name: /PDF/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('nessuna ora registrata nel periodo')
    expect(downloadDocument).not.toHaveBeenCalled()
  })
})
