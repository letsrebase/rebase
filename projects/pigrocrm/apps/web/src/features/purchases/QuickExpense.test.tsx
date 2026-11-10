import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { toast } from '@rebase/ui/sonner'
import { api, fetchWithRefresh } from '@/lib/api'
import { toIsoDate } from '@/lib/dates'
import { QuickExpense } from './QuickExpense'
import { normaliseAmount } from './receiptPhoto'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
    fetchWithRefresh: vi.fn(),
  }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }))

const GET = vi.mocked(api.GET) as unknown as Mock
const POST = vi.mocked(api.POST) as unknown as Mock
const upload = vi.mocked(fetchWithRefresh)

const CATEGORIES = [
  { id: 'cat-old', name: 'Vecchia', archived: true, position: 0, code: null },
  { id: 'cat-1', name: 'Trasferte', archived: false, position: 1, code: null },
  { id: 'cat-2', name: 'Cancelleria', archived: false, position: 2, code: null },
]

const answer = (data: unknown) =>
  Promise.resolve({ data, response: new Response(null, { status: 200 }) })
const refusal = (status: number, problem: Record<string, unknown>) =>
  Promise.resolve({ error: problem, response: new Response(null, { status }) })
const problem = (detail: string, extra: Record<string, unknown> = {}) => ({
  detail,
  title: 'x',
  status: 409,
  instance: '/',
  code: 'conflict',
  ...extra,
})
const stored = (id = 'item-1') =>
  new Response(JSON.stringify({ items: [{ id }] }), { status: 201 })

function open() {
  const onOpenChange = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <QuickExpense open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  )
  return onOpenChange
}

async function fill(amount = '12,50', merchant = 'Bar Roma') {
  await userEvent.type(await screen.findByLabelText('Importo (€)'), amount)
  await userEvent.type(screen.getByLabelText('Esercente'), merchant)
}

const register = () => userEvent.click(screen.getByRole('button', { name: 'Registra' }))
const today = () => toIsoDate(new Date())
const photo = () => new File(['x'], 'scontrino.jpg', { type: 'image/jpeg' })

beforeEach(() => {
  GET.mockReset().mockImplementation(() => answer(CATEGORIES))
  POST.mockReset().mockImplementation((_path: string, init: { body: Record<string, unknown> }) =>
    answer({ id: 'p1', deal_id: null, ...init.body }),
  )
  upload.mockReset()
  vi.mocked(toast.success).mockReset()
})

describe('«Spesa» sheet', () => {
  it('draws amount, esercente, categoria, data and an optional photo, and no «Già pagata»', async () => {
    open()
    expect(await screen.findByRole('dialog', { name: 'Spesa' })).toBeInTheDocument()
    expect(screen.getByText('Uno scontrino o una spesa, in due tocchi.')).toBeInTheDocument()
    expect(await screen.findByLabelText('Importo (€)')).toHaveAttribute('inputmode', 'decimal')
    expect(screen.getByLabelText('Esercente')).toBeInTheDocument()
    // The first active category is the default; an archived one is not offered.
    expect(screen.getByRole('combobox', { name: 'Categoria' })).toHaveTextContent('Trasferte')
    expect(screen.getByLabelText('Data')).toHaveValue(today())
    const file = screen.getByLabelText('Foto (facoltativa)')
    expect(file).toHaveAttribute('type', 'file')
    expect(file).toHaveAttribute('accept', 'image/*')
    // No camera-first step: the person may take a photo or skip it.
    expect(file).not.toHaveAttribute('capture')
    // A purchase has no due item yet: nothing to choose.
    expect(screen.queryByText(/già pagata/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/da pagare/i)).not.toBeInTheDocument()
  })

  it('waits for the categories with the shared loading state', async () => {
    GET.mockImplementation(() => new Promise(() => {}))
    open()
    expect(await screen.findByRole('status', { name: 'Carico le categorie…' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Registra' })).not.toBeInTheDocument()
  })

  it('says why the categories are missing, in the server’s words, and asks again on «Riprova»', async () => {
    GET.mockImplementationOnce(() => refusal(500, problem('Non riesco a leggere le categorie.')))
    open()
    expect(await screen.findByText('Non riesco a leggere le categorie.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(await screen.findByLabelText('Esercente')).toBeInTheDocument()
  })

  it('registers a receipt with no deal and a dot-decimal amount from «12,50», then closes', async () => {
    const onOpenChange = open()
    await fill('12,50', '  Bar Roma ')
    await register()

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(POST).toHaveBeenCalledTimes(1)
    expect(POST).toHaveBeenCalledWith('/api/purchases', {
      body: {
        kind: 'receipt',
        category_id: 'cat-1',
        date: today(),
        amount: '12.50',
        merchant_name: 'Bar Roma',
        description: 'Bar Roma',
        deal_id: null,
      },
    })
    expect(upload).not.toHaveBeenCalled()
    expect(toast.success).toHaveBeenCalledWith('Spesa registrata')
  })

  it('sends a dot amount and a whole one as typed', async () => {
    open()
    await fill('7.5', 'Edicola')
    await register()
    await waitFor(() => expect(POST).toHaveBeenCalled())
    expect(POST.mock.calls[0]?.[1].body.amount).toBe('7.5')
    expect(normaliseAmount('42')).toBe('42')
    expect(normaliseAmount('1.234,50')).toBeNull()
    expect(normaliseAmount('')).toBeNull()
    expect(normaliseAmount('otto')).toBeNull()
    expect(normaliseAmount('-3')).toBeNull()
  })

  it('does not ask the server for what is not an amount or has no esercente', async () => {
    open()
    await userEvent.type(await screen.findByLabelText('Importo (€)'), 'dodici')
    await register()
    expect(await screen.findByText('Scrivi un importo, per esempio 12,50.')).toBeInTheDocument()
    expect(screen.getByText('Scrivi dove hai speso.')).toBeInTheDocument()
    expect(POST).not.toHaveBeenCalled()
  })

  it('uploads the photo unread before writing the purchase, and links the item', async () => {
    const order: string[] = []
    upload.mockImplementation(async () => {
      order.push('upload')
      return stored('item-9')
    })
    POST.mockImplementation((_path: string, init: { body: Record<string, unknown> }) => {
      order.push('create')
      return answer({ id: 'p1', deal_id: null, ...init.body })
    })
    const onOpenChange = open()
    await fill()
    await userEvent.upload(screen.getByLabelText('Foto (facoltativa)'), photo())
    await register()

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(order).toEqual(['upload', 'create'])
    const [url, init] = upload.mock.calls[0] ?? []
    expect(url).toBe('/api/inbox/upload?read=false')
    expect(init?.method).toBe('POST')
    expect((init?.body as FormData).get('file')).toBeInstanceOf(File)
    expect(POST.mock.calls[0]?.[1].body).toMatchObject({
      kind: 'receipt',
      deal_id: null,
      inbox_item_id: 'item-9',
    })
  })

  it('does not write the purchase when the photo is not stored, and says so', async () => {
    upload.mockResolvedValue(
      new Response(JSON.stringify(problem('Formato non accettato.', { status: 422 })), {
        status: 422,
      }),
    )
    const onOpenChange = open()
    await fill()
    await userEvent.upload(screen.getByLabelText('Foto (facoltativa)'), photo())
    await register()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'La foto non è stata caricata e la spesa non è stata registrata: Formato non accettato.',
    )
    expect(POST).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    // Nothing the person typed is lost, and the button is live again.
    expect(screen.getByLabelText('Esercente')).toHaveValue('Bar Roma')
    expect(screen.getByRole('button', { name: 'Registra' })).toBeEnabled()
  })

  it('keeps the sheet open with the server’s words, and a retry links the stored photo once', async () => {
    upload.mockImplementation(async () => stored('item-3'))
    POST.mockImplementationOnce(() => refusal(409, problem('Il periodo è chiuso.')))
    const onOpenChange = open()
    await fill()
    await userEvent.upload(screen.getByLabelText('Foto (facoltativa)'), photo())
    await register()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Il periodo è chiuso. La foto è già in In arrivo.',
    )
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()

    await register()
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    expect(upload).toHaveBeenCalledTimes(1)
    expect(POST).toHaveBeenCalledTimes(2)
    expect(POST.mock.calls[1]?.[1].body.inbox_item_id).toBe('item-3')
  })

  it('shows a refusal that names a field on that field', async () => {
    POST.mockImplementationOnce(() =>
      refusal(
        422,
        problem('Importo nullo', {
          status: 422,
          code: 'validation_failed',
          field: 'amount',
          reason: 'importo nullo',
        }),
      ),
    )
    open()
    await fill('0', 'Bar')
    await register()
    expect(await screen.findByText('importo nullo')).toBeInTheDocument()
    expect(screen.getByLabelText('Importo (€)')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says the photo is already in «In arrivo» when a field refusal follows its upload', async () => {
    upload.mockImplementation(async () => stored())
    POST.mockImplementationOnce(() =>
      refusal(422, problem('x', { status: 422, code: 'validation_failed', field: 'amount', reason: 'importo nullo' })),
    )
    open()
    await fill('0', 'Bar')
    await userEvent.upload(screen.getByLabelText('Foto (facoltativa)'), photo())
    await register()
    expect(await screen.findByText('importo nullo')).toBeInTheDocument()
    expect(screen.getByText('La foto è già in In arrivo.')).toBeInTheDocument()
  })

  it('lets the person drop the photo and register without it', async () => {
    open()
    await fill()
    await userEvent.upload(screen.getByLabelText('Foto (facoltativa)'), photo())
    await userEvent.click(screen.getByRole('button', { name: 'Togli la foto' }))
    await register()
    await waitFor(() => expect(POST).toHaveBeenCalled())
    expect(upload).not.toHaveBeenCalled()
    expect(POST.mock.calls[0]?.[1].body).not.toHaveProperty('inbox_item_id')
  })
})
