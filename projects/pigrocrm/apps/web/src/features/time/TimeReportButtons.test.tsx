import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { downloadDocument } from '@/features/documents/queries'
import { downloadFromApi } from '@/lib/download'
import { TimeReportButtons } from './TimeReportButtons'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { POST: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock('@/features/documents/queries', () => ({ downloadDocument: vi.fn() }))
vi.mock('@/lib/download', () => ({ downloadFromApi: vi.fn().mockResolvedValue(undefined) }))

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
  vi.mocked(api.POST).mockReset()
  vi.mocked(downloadDocument).mockReset()
  vi.mocked(downloadDocument).mockResolvedValue(undefined)
})

describe('TimeReportButtons', () => {
  /** The XLSX goes through the refreshing, space-prefixed download, not a bare link. */
  it('downloads the XLSX through the API', async () => {
    renderButtons()
    await userEvent.click(screen.getByRole('button', { name: /XLSX/ }))
    expect(downloadFromApi).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/deals\/d-1\/time-report\?mese=/),
    )
  })

  /**
   * The PDF archives a document and answers `201 application/json`, so it is a `POST`
   * whose answer is read, followed by a download of the archived document -- the only
   * path that carries authorisation on either storage backend. As an anchor, clicking
   * «PDF» navigated the browser to a page of JSON.
   */
  it('renders the PDF and then downloads the archived document, never linking to JSON', async () => {
    vi.mocked(api.POST).mockImplementation(() => ok(DOCUMENT, 201))
    renderButtons()

    expect(screen.queryByRole('link', { name: /^PDF$/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /PDF/ }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { params: { query: Record<string, string> } },
    ]
    expect(path).toBe('/api/deals/{deal_id}/time-report/pdf')
    expect(options.params.query.mese).toMatch(/^\d{4}-\d{2}$/)
    await waitFor(() => expect(downloadDocument).toHaveBeenCalledWith('doc-9'))
  })

  /** The month the user picked is the month that gets rendered -- not today's. */
  it('sends the chosen month', async () => {
    vi.mocked(api.POST).mockImplementation(() => ok(DOCUMENT, 201))
    renderButtons()

    const month = screen.getByLabelText('Rapporto ore del mese')
    await userEvent.clear(month)
    await userEvent.type(month, '2026-03')
    await userEvent.click(screen.getByRole('button', { name: /PDF/ }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
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
    vi.mocked(api.POST).mockImplementation(() =>
      failed({ detail: 'nessuna ora registrata nel periodo' }, 422),
    )
    renderButtons()

    await userEvent.click(screen.getByRole('button', { name: /PDF/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('nessuna ora registrata nel periodo')
    expect(downloadDocument).not.toHaveBeenCalled()
  })
})
