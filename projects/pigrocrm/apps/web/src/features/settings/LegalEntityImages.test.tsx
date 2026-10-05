import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AziendaImages } from './AziendaImages'
import type { AziendaRecord } from './queries'
import { api, fetchWithRefresh } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), DELETE: vi.fn() }, fetchWithRefresh: vi.fn() }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const AZIENDA = {
  id: 'a-1',
  nome: 'humancraft',
  ragione_sociale: 'Humancraft S.r.l.',
  logo_key: null,
  firma_key: null,
} as AziendaRecord

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

function imageResponse() {
  return new Response(PNG, { status: 200, headers: { 'content-type': 'image/png' } })
}

function problemResponse(detail: string) {
  return new Response(
    JSON.stringify({ type: 'about:blank', title: 'Dati non validi', status: 422, detail, code: 'validation_failed', field: 'file' }),
    { status: 422, headers: { 'content-type': 'application/problem+json' } },
  )
}

function renderImages(azienda: AziendaRecord = AZIENDA) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AziendaImages azienda={azienda} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(fetchWithRefresh).mockReset()
  vi.mocked(api.DELETE).mockReset()
  let n = 0
  URL.createObjectURL = vi.fn(() => `blob:image-${++n}`)
  URL.revokeObjectURL = vi.fn()
})

describe('AziendaImages', () => {
  it('shows both blocks empty, with «Carica» and no «Rimuovi», and asks for no bytes', () => {
    renderImages()
    expect(screen.getByText('Nessun logo')).toBeInTheDocument()
    expect(screen.getByText('Nessuna firma')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Carica' })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Rimuovi' })).not.toBeInTheDocument()
    expect(fetchWithRefresh).not.toHaveBeenCalled()
  })

  it('reads the logo the row says is there and shows it, with «Sostituisci» and «Rimuovi»', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(imageResponse())
    renderImages({ ...AZIENDA, logo_key: 'aziende/a-1/logo.png' })
    const img = await screen.findByRole('img', { name: 'Logo di humancraft' })
    expect(img).toHaveAttribute('src', 'blob:image-1')
    expect(fetchWithRefresh).toHaveBeenCalledWith('/api/aziende/a-1/logo')
    expect(screen.getByRole('button', { name: 'Sostituisci' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rimuovi' })).toBeInTheDocument()
    // The signature block is untouched: nothing was asked for it.
    expect(fetchWithRefresh).toHaveBeenCalledTimes(1)
  })

  it('uploads the chosen file as multipart to the azienda\'s own route', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(JSON.stringify({ ...AZIENDA, logo_key: 'aziende/a-1/logo.png' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    renderImages()
    const file = new File([PNG], 'marchio.png', { type: 'image/png' })
    await userEvent.upload(screen.getByLabelText('Carica logo'), file)
    await waitFor(() => expect(fetchWithRefresh).toHaveBeenCalledTimes(1))
    const [path, init] = vi.mocked(fetchWithRefresh).mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/aziende/a-1/logo')
    expect(init.method).toBe('PUT')
    expect((init.body as FormData).get('file')).toBe(file)
  })

  it('shows the server\'s refusal under the control', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(problemResponse('il file non è un PNG né un SVG'))
    renderImages()
    // `applyAccept: false`: the picker's own filter would keep a JPEG out before the
    // server ever saw it, and this test is about the server's sentence.
    await userEvent.upload(
      screen.getByLabelText('Carica firma'),
      new File([new Uint8Array([0xff, 0xd8])], 'foto.jpg', { type: 'image/jpeg' }),
      { applyAccept: false },
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('il file non è un PNG né un SVG')
  })

  it('removes through the typed client and the azienda\'s id', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(imageResponse())
    vi.mocked(api.DELETE).mockResolvedValue({
      data: { ...AZIENDA, firma_key: null },
      response: new Response(null, { status: 200 }),
    } as never)
    renderImages({ ...AZIENDA, firma_key: 'aziende/a-1/firma.png' })
    await screen.findByRole('img', { name: 'Firma di humancraft' })
    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi' }))
    await waitFor(() => expect(api.DELETE).toHaveBeenCalledTimes(1))
    const [path, options] = vi.mocked(api.DELETE).mock.calls[0] as unknown as [
      string,
      { params: { path: { azienda_id: string } } },
    ]
    expect(path).toBe('/api/aziende/{azienda_id}/firma')
    expect(options.params.path.azienda_id).toBe('a-1')
  })

  it("refuses a file over 1 MiB before it travels, with the server's own sentence", async () => {
    renderImages()
    const big = new File([new Uint8Array(1024 * 1024 + 1)], 'enorme.png', { type: 'image/png' })
    await userEvent.upload(screen.getByLabelText('Carica logo'), big)
    expect(await screen.findByRole('alert')).toHaveTextContent('il file supera 1024 KiB')
    expect(fetchWithRefresh).not.toHaveBeenCalled()
  })

  it('says when the stored image could not be read, instead of showing an empty block', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(
        JSON.stringify({ type: 'about:blank', title: 'Errore', status: 503, detail: 'archivio non raggiungibile', code: 'storage_unavailable' }),
        { status: 503, headers: { 'content-type': 'application/problem+json' } },
      ),
    )
    renderImages({ ...AZIENDA, logo_key: 'aziende/a-1/logo.png' })
    expect(await screen.findByRole('alert')).toHaveTextContent('archivio non raggiungibile')
  })
})
