import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { PurchaseOrderEditor } from './PurchaseOrderEditor'
import type { Invoice } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const PROFORMA = {
  id: 'pf-1',
  tipo: 'proforma',
  stato: 'bozza',
  ordine_numero: 'ODA-14',
  ordine_data: '2026-10-09',
  ordine_cig: null,
  ordine_cup: null,
} as unknown as Invoice

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

function sent(): Record<string, unknown> {
  const [first] = vi.mocked(api.PATCH).mock.calls
  if (!first) throw new Error('PATCH /api/invoices/{invoice_id} was never called')
  return (first[1] as { body: Record<string, unknown> }).body
}

beforeEach(() => {
  vi.mocked(api.PATCH).mockResolvedValue(ok(PROFORMA))
})

afterEach(() => {
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(toast.success).mockReset()
})

describe('PurchaseOrderEditor', () => {
  it('starts from the stored order', () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    expect(screen.getByLabelText('Numero ordine')).toHaveValue('ODA-14')
    expect(screen.getByLabelText('Data ordine')).toHaveValue('2026-10-09')
    expect(screen.getByLabelText('CIG')).toHaveValue('')
    expect(screen.getByLabelText('CUP')).toHaveValue('')
  })

  it('saves all four fields through the PATCH, null for an empty one', async () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('CIG', ' z0a1b2c3d4 ')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(vi.mocked(api.PATCH).mock.calls[0]?.[0]).toBe('/api/invoices/{invoice_id}')
    expect(sent()).toEqual({
      ordine_numero: 'ODA-14',
      ordine_data: '2026-10-09',
      ordine_cig: 'z0a1b2c3d4',
      ordine_cup: null,
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Ordine salvato'))
  })

  it('shows what the server saved after a successful PATCH', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(ok({ ...PROFORMA, ordine_cig: 'Z0A1B2C3D4' }))
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('CIG', ' z0a1b2c3d4 ')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Ordine salvato'))
    expect(screen.getByLabelText('CIG')).toHaveValue('Z0A1B2C3D4')
  })

  it('does not cut what is pasted: the number is sent trimmed and whole', async () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    for (const label of ['Numero ordine', 'CIG', 'CUP']) {
      expect(screen.getByLabelText(label)).not.toHaveAttribute('maxlength')
    }
    type('Numero ordine', '  ODA-1234567890123456789 ')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(sent().ordine_numero).toBe('ODA-1234567890123456789')
  })

  it('refuses a CIG without the order number before any request', async () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('Numero ordine', '')
    type('CIG', 'Z0A1B2C3D4')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    expect(screen.getByText('Indica il numero dell’ordine.')).toBeInTheDocument()
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('clears the whole order when every field is emptied', async () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('Numero ordine', '')
    type('Data ordine', '')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(sent()).toEqual({ ordine_numero: null, ordine_data: null, ordine_cig: null, ordine_cup: null })
  })

  it('shows the server’s own refusal of a malformed CIG', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(
      failed(
        {
          type: 'https://pigrocrm.dev/errors/validation_failed',
          title: 'Dati non validi',
          status: 422,
          detail: 'il CIG ha 10 caratteri, lettere e cifre',
          code: 'validation_failed',
          field: 'ordine_cig',
        },
        422,
      ),
    )
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('CIG', 'Z0A1')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('il CIG ha 10 caratteri')
    expect(toast.success).not.toHaveBeenCalled()
  })
})
