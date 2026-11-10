/**
 * What the accountant opens from a share link (REB-1047): a page that says what the link is before
 * it downloads, one button per ZIP, and the server's own sentence when the link is over. The token
 * goes to the server in a POST body and nowhere else.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { saveResponse } from '@/lib/download'
import { SharedExportPage } from './SharedExportPage'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { POST: vi.fn() } }
})
vi.mock('@/lib/download', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/download')>()
  return { ...actual, saveResponse: vi.fn(() => Promise.resolve()) }
})

const SHARED = {
  company: 'Lumen Studio S.r.l.',
  period_from: '2026-07-01',
  period_to: '2026-09-30',
  built_at: '2026-10-12T09:40:00Z',
  shared_by: 'Anna Rossi',
  expires_at: '2026-10-20T08:00:00Z',
  parts: [{ part: 1, filename: 'commercialista-lumen.zip', size_bytes: 40_265_318 }],
}

const fetched = vi.fn()

beforeEach(() => {
  vi.mocked(api.POST).mockReset()
  vi.mocked(saveResponse).mockClear()
  fetched.mockReset()
  vi.stubGlobal('fetch', fetched)
})
afterEach(() => vi.unstubAllGlobals())

describe('the page of a share link', () => {
  it('says what the link is, who shared it and when it ends, before anything downloads', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: SHARED, response: new Response(null, { status: 200 }) } as never)
    render(<SharedExportPage token="tok-123" />)
    expect(await screen.findByText('Esportazione per il commercialista')).toBeInTheDocument()
    expect(screen.getByText(/Lumen Studio S\.r\.l\. · 3° trimestre 2026/)).toBeInTheDocument()
    expect(screen.getByText(/Condivisa da Anna Rossi/)).toBeInTheDocument()
    expect(screen.getByText(/scade il 20\/10\/2026/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Scarica lo ZIP/ })).toHaveTextContent('38,4 MiB')
    expect(vi.mocked(api.POST).mock.calls[0]).toEqual([
      '/api/accountant-export/shared/describe',
      { body: { token: 'tok-123' } },
    ])
    expect(fetched).not.toHaveBeenCalled()
    // No navigation and no account prompt.
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByRole('navigation')).toBeNull()
  })

  it('downloads a ZIP with the token in the body and never in the address', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: SHARED, response: new Response(null, { status: 200 }) } as never)
    fetched.mockResolvedValue(new Response('PK', { status: 200 }))
    const user = userEvent.setup()
    render(<SharedExportPage token="tok-123" />)
    await user.click(await screen.findByRole('button', { name: /Scarica lo ZIP/ }))
    await waitFor(() => expect(saveResponse).toHaveBeenCalledTimes(1))
    const [url, init] = fetched.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/accountant-export/shared/download')
    expect(url).not.toContain('tok-123')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ token: 'tok-123', part: 1 })
  })

  it('offers one button per file of a split export', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      data: { ...SHARED, parts: [...SHARED.parts, { part: 2, filename: 'p2.zip', size_bytes: 2_048 }] },
      response: new Response(null, { status: 200 }),
    } as never)
    render(<SharedExportPage token="tok-123" />)
    expect(await screen.findByText(/2 file ZIP/)).toBeInTheDocument()
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Scarica il file 1 di 238,4 MiB',
      'Scarica il file 2 di 22,0 KiB',
    ])
  })

  it('says the link is over in the server’s own words', async () => {
    vi.mocked(api.POST).mockResolvedValue({
      error: { code: 'export_link_revoked', detail: 'Questo link è stato revocato: chiedi a chi te l’ha mandato di rifarlo.' },
      response: new Response(null, { status: 410 }),
    } as never)
    render(<SharedExportPage token="tok-123" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Questo link è stato revocato')
    expect(screen.getByText('Il link non funziona')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('says so when the address carried no token at all, without asking the server', async () => {
    render(<SharedExportPage token="" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Questo link non è completo')
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('shows a download the server refuses and lets the person try again', async () => {
    vi.mocked(api.POST).mockResolvedValue({ data: SHARED, response: new Response(null, { status: 200 }) } as never)
    fetched.mockResolvedValue(
      new Response(JSON.stringify({ code: 'export_link_expired', detail: 'Questo link è scaduto.' }), { status: 410 }),
    )
    const user = userEvent.setup()
    render(<SharedExportPage token="tok-123" />)
    const button = await screen.findByRole('button', { name: /Scarica lo ZIP/ })
    await user.click(button)
    expect(await screen.findByRole('alert')).toHaveTextContent('Questo link è scaduto.')
    expect(saveResponse).not.toHaveBeenCalled()
    expect(button).toBeEnabled()
  })
})
