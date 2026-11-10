/**
 * «Carica estratto conto» (REB-1025): the file, the period only when the server says the file
 * does not state it, which account when the caller names none, and what was read afterwards.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, fetchWithRefresh } from '@/lib/api'
import { UploadStatementDialog } from './UploadStatementDialog'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() }, fetchWithRefresh: vi.fn() }
})

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

const RESULT = {
  statement: { id: 'st-1' },
  added: 41,
  duplicates: 2,
  proposed: 3,
}

const FILE = new File(['<Document/>'], 'estratto.xml', { type: 'text/xml' })

function renderDialog(accountId?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onOpenChange = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <UploadStatementDialog open onOpenChange={onOpenChange} accountId={accountId} />
    </QueryClientProvider>,
  )
  return onOpenChange
}

describe('UploadStatementDialog', () => {
  beforeEach(() => {
    vi.mocked(fetchWithRefresh).mockReset()
    vi.mocked(api.GET).mockReset()
  })

  it('sends the file as multipart to the account and says what was read', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(json(201, RESULT))
    const user = userEvent.setup()
    renderDialog('acc-1')
    const input = screen.getByLabelText('File dell’estratto conto') as HTMLInputElement
    expect(input.accept).toContain('.xml')
    expect(input.accept).toContain('.xlsx')
    expect(screen.queryByLabelText('Periodo dal')).toBeNull()
    await user.upload(input, FILE)
    await user.click(screen.getByRole('button', { name: 'Carica' }))
    expect(await screen.findByText('41 movimenti, 2 già presenti, 3 con una proposta')).toBeTruthy()
    const [path, init] = vi.mocked(fetchWithRefresh).mock.calls[0]!
    expect(path).toBe('/api/bank-accounts/acc-1/statements')
    const body = init?.body as FormData
    expect((body.get('file') as File).name).toBe('estratto.xml')
    expect(body.has('period_from')).toBe(false)
  })

  it('asks for the period only when the server answers that the file does not state it', async () => {
    vi.mocked(fetchWithRefresh)
      .mockResolvedValueOnce(
        json(422, {
          type: 'about:blank',
          title: 'Dati non validi',
          status: 422,
          detail: 'il periodo coperto va indicato',
          code: 'validation_failed',
          entity: 'bank_statement',
          field: 'period',
          reason: 'il periodo coperto va indicato',
        }),
      )
      .mockResolvedValueOnce(json(201, { ...RESULT, added: 1, duplicates: 0, proposed: 1 }))
    const user = userEvent.setup()
    renderDialog('acc-1')
    await user.upload(screen.getByLabelText('File dell’estratto conto'), FILE)
    await user.click(screen.getByRole('button', { name: 'Carica' }))
    expect(await screen.findByText('Il file non dichiara il periodo: va indicato, non si indovina.')).toBeTruthy()
    const send = screen.getByRole('button', { name: 'Carica' })
    expect((send as HTMLButtonElement).disabled).toBe(true)
    await user.type(screen.getByLabelText('Periodo dal'), '2026-10-01')
    await user.type(screen.getByLabelText('Periodo al'), '2026-10-31')
    await user.click(send)
    expect(await screen.findByText('1 movimento, 0 già presenti, 1 con una proposta')).toBeTruthy()
    const body = vi.mocked(fetchWithRefresh).mock.calls[1]![1]?.body as FormData
    expect(body.get('period_from')).toBe('2026-10-01')
    expect(body.get('period_to')).toBe('2026-10-31')
  })

  it('says an unreadable file in the server’s sentence', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      json(422, {
        type: 'about:blank',
        title: 'Dati non validi',
        status: 422,
        detail: 'formato non riconosciuto',
        code: 'validation_failed',
        field: 'file',
        reason: 'formato non riconosciuto',
      }),
    )
    const user = userEvent.setup()
    renderDialog('acc-1')
    await user.upload(screen.getByLabelText('File dell’estratto conto'), FILE)
    await user.click(screen.getByRole('button', { name: 'Carica' }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'formato non riconosciuto')
    expect(screen.queryByLabelText('Periodo dal')).toBeNull()
  })

  it('asks which account when none is named, and sends to the one chosen', async () => {
    vi.mocked(api.GET).mockResolvedValue({
      data: [
        { id: 'acc-1', name: 'Conto operativo', bank: 'Banco BPM', archived_at: null },
        { id: 'acc-2', name: 'Conto USD', bank: 'Qonto', archived_at: null },
      ],
      response: new Response(),
    } as never)
    vi.mocked(fetchWithRefresh).mockResolvedValue(json(201, RESULT))
    const user = userEvent.setup()
    renderDialog()
    await user.upload(screen.getByLabelText('File dell’estratto conto'), FILE)
    expect((screen.getByRole('button', { name: 'Carica' }) as HTMLButtonElement).disabled).toBe(true)
    await user.click(await screen.findByRole('combobox', { name: 'Conto' }))
    await user.click(await screen.findByRole('option', { name: 'Conto USD · Qonto' }))
    await user.click(screen.getByRole('button', { name: 'Carica' }))
    await waitFor(() => {
      expect(vi.mocked(fetchWithRefresh).mock.calls[0]![0]).toBe('/api/bank-accounts/acc-2/statements')
    })
  })

  it('keeps a file over 10 MiB in the browser', async () => {
    const user = userEvent.setup()
    renderDialog('acc-1')
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'grande.xml')
    await user.upload(screen.getByLabelText('File dell’estratto conto'), big)
    expect(screen.getByText('Il file supera i 10 MiB.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Carica' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
