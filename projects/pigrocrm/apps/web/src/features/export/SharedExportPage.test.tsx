/**
 * What the accountant opens from a share link (REB-1047): a page that says what the link is before
 * it downloads, one button per ZIP, and the server's own sentence when the link is over. The token
 * goes to the server in a POST body and nowhere else.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
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
  requires_code: false,
  recipient_hint: null,
  unlocked: true,
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

  describe('a link that asks a one-time code', () => {
    const LOCKED = {
      ...SHARED,
      requires_code: true,
      recipient_hint: 'p…@studio-esempio.it',
      unlocked: false,
      parts: [],
    }
    const UNLOCKED = { ...SHARED, requires_code: true, recipient_hint: 'p…@studio-esempio.it', unlocked: true }
    const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) })

    /** The server: a locked link until the right code, then the files for the access it gave. */
    function serve(verify: unknown = ok({ access: 'sess-9' })) {
      vi.mocked(api.POST).mockImplementation(((path: string, init: { body: { access?: string } }) => {
        if (path === '/api/accountant-export/shared/describe') {
          return Promise.resolve(ok(init.body.access === 'sess-9' ? UNLOCKED : LOCKED))
        }
        if (path === '/api/accountant-export/shared/code') return Promise.resolve(ok({ sent_to: 'p…@studio-esempio.it' }))
        if (path === '/api/accountant-export/shared/verify') return Promise.resolve(verify)
        throw new Error(`unexpected ${path}`)
      }) as never)
    }

    it('shows no file and asks for the code, which is mailed only when requested', async () => {
      serve()
      const user = userEvent.setup()
      render(<SharedExportPage token="tok-123" />)
      expect(await screen.findByText(/ti mandiamo un codice a p…@studio-esempio\.it/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Scarica/ })).toBeNull()
      expect(screen.queryByRole('group', { name: 'Codice' })).toBeNull()
      expect(api.POST).toHaveBeenCalledTimes(1)

      await user.click(screen.getByRole('button', { name: 'Invia il codice' }))
      expect(await screen.findByText(/Ti abbiamo scritto un codice a p…@studio-esempio\.it/)).toBeInTheDocument()
      expect(vi.mocked(api.POST).mock.calls[1]).toEqual([
        '/api/accountant-export/shared/code',
        { body: { token: 'tok-123' } },
      ])
      const group = screen.getByRole('group', { name: 'Codice' })
      expect(within(group).getAllByRole('textbox')).toHaveLength(6)
      expect(screen.getByRole('button', { name: 'Apri i file' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Invia un altro codice' })).toBeEnabled()
    })

    it('opens the files with a right code and sends the access with every download', async () => {
      serve()
      fetched.mockResolvedValue(new Response('PK', { status: 200 }))
      const user = userEvent.setup()
      render(<SharedExportPage token="tok-123" />)
      await user.click(await screen.findByRole('button', { name: 'Invia il codice' }))
      await screen.findByRole('group', { name: 'Codice' })
      // Pasting six digits fills every box.
      await user.click(screen.getByRole('textbox', { name: 'Cifra 1 di 6' }))
      await user.paste('123456')
      expect(screen.getAllByRole('textbox').map((box) => (box as HTMLInputElement).value).join('')).toBe('123456')
      await user.click(screen.getByRole('button', { name: 'Apri i file' }))

      expect(await screen.findByRole('button', { name: /Scarica lo ZIP/ })).toBeInTheDocument()
      const calls = vi.mocked(api.POST).mock.calls as unknown as [string, { body: unknown }][]
      expect(calls.find(([path]) => path.endsWith('/verify'))?.[1]).toEqual({ body: { token: 'tok-123', code: '123456' } })
      expect(calls.filter(([path]) => path.endsWith('/describe')).at(-1)?.[1]).toEqual({
        body: { token: 'tok-123', access: 'sess-9' },
      })
      await user.click(screen.getByRole('button', { name: /Scarica lo ZIP/ }))
      await waitFor(() => expect(saveResponse).toHaveBeenCalledTimes(1))
      const [url, init] = fetched.mock.calls[0] as [string, RequestInit]
      expect(url).not.toContain('sess-9')
      expect(JSON.parse(init.body as string)).toEqual({ token: 'tok-123', part: 1, access: 'sess-9' })
      expect(document.body.textContent).not.toContain('sess-9')
    })

    it('types digit by digit, moving on and going back with Backspace', async () => {
      serve()
      const user = userEvent.setup()
      render(<SharedExportPage token="tok-123" />)
      await user.click(await screen.findByRole('button', { name: 'Invia il codice' }))
      const boxes = await screen.findAllByRole('textbox')
      await user.keyboard('12')
      expect(boxes[2]).toHaveFocus()
      expect(boxes.slice(0, 3).map((box) => (box as HTMLInputElement).value)).toEqual(['1', '2', ''])
      await user.keyboard('{Backspace}')
      expect(boxes[1]).toHaveFocus()
      expect((boxes[1] as HTMLInputElement).value).toBe('')
    })

    it('shows the server’s sentence for a wrong code and clears the boxes', async () => {
      serve({
        error: { code: 'export_code_invalid', detail: 'Il codice non è giusto o è scaduto: chiedine un altro.' },
        response: new Response(null, { status: 403 }),
      })
      const user = userEvent.setup()
      render(<SharedExportPage token="tok-123" />)
      await user.click(await screen.findByRole('button', { name: 'Invia il codice' }))
      await user.click(await screen.findByRole('textbox', { name: 'Cifra 1 di 6' }))
      await user.paste('000000')
      await user.click(screen.getByRole('button', { name: 'Apri i file' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('Il codice non è giusto o è scaduto')
      for (const box of screen.getAllByRole('textbox')) expect(box).toHaveValue('')
      expect(screen.queryByRole('button', { name: /Scarica/ })).toBeNull()
    })

    it('says so when too many codes were asked', async () => {
      vi.mocked(api.POST).mockImplementation(((path: string) =>
        Promise.resolve(
          path.endsWith('/describe')
            ? ok(LOCKED)
            : {
                error: { code: 'export_code_throttled', detail: 'Hai chiesto troppi codici: riprova tra qualche minuto.' },
                response: new Response(null, { status: 429 }),
              },
        )) as never)
      const user = userEvent.setup()
      render(<SharedExportPage token="tok-123" />)
      await user.click(await screen.findByRole('button', { name: 'Invia il codice' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('Hai chiesto troppi codici')
      expect(screen.getByRole('button', { name: 'Invia il codice' })).toBeEnabled()
    })

    it('goes back to the code step when the session of the code is over', async () => {
      serve()
      fetched.mockResolvedValue(
        new Response(JSON.stringify({ code: 'export_code_required', detail: 'Serve il codice.' }), { status: 403 }),
      )
      const user = userEvent.setup()
      render(<SharedExportPage token="tok-123" />)
      await user.click(await screen.findByRole('button', { name: 'Invia il codice' }))
      await user.click(await screen.findByRole('textbox', { name: 'Cifra 1 di 6' }))
      await user.paste('123456')
      await user.click(screen.getByRole('button', { name: 'Apri i file' }))
      await user.click(await screen.findByRole('button', { name: /Scarica lo ZIP/ }))
      expect(await screen.findByRole('alert')).toHaveTextContent('La sessione è scaduta')
      expect(screen.getByRole('button', { name: 'Invia il codice' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Scarica/ })).toBeNull()
    })
  })
})
