import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { InvoiceHeaderEditor } from './InvoiceHeaderEditor'
import type { Invoice } from './queries'

// The house pattern (`InvoiceLinesEditor.test.tsx`): the client's methods are replaced,
// `unwrap`/`toProblem` stay real, and what is asserted is the body that reached
// `api.PATCH`, one layer before the transport.
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

const DRAFT = {
  id: 'inv-1',
  tipo: 'fattura',
  stato: 'bozza',
  data_emissione: null,
  competenza_da: null,
  competenza_a: null,
} as unknown as Invoice

const PROFORMA = {
  id: 'pf-1',
  tipo: 'proforma',
  stato: 'bozza',
  data_emissione: '2026-09-09',
  competenza_da: '2026-08-01',
  competenza_a: '2026-08-31',
} as unknown as Invoice

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

function setDate(label: string | RegExp, value: string) {
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

describe('InvoiceHeaderEditor', () => {
  it('asks a proforma for its date and starts from the stored one', () => {
    wrap(<InvoiceHeaderEditor invoice={PROFORMA} />)

    expect(screen.getByLabelText('Data')).toHaveValue('2026-09-09')
    expect(screen.getByLabelText('Competenza dal')).toHaveValue('2026-08-01')
    expect(screen.getByLabelText('Competenza al')).toHaveValue('2026-08-31')
  })

  it('offers a draft fattura no date, since emission assigns it', () => {
    wrap(<InvoiceHeaderEditor invoice={DRAFT} />)

    expect(screen.queryByLabelText('Data')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Competenza dal')).toHaveValue('')
    expect(screen.getByText(/assegnata all’emissione/)).toBeInTheDocument()
  })

  it('saves the proforma’s date and its period through the PATCH', async () => {
    wrap(<InvoiceHeaderEditor invoice={PROFORMA} />)
    setDate('Data', '2026-09-01')
    setDate('Competenza dal', '2026-07-01')
    setDate('Competenza al', '2026-07-31')
    await userEvent.click(screen.getByRole('button', { name: 'Salva date' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(vi.mocked(api.PATCH).mock.calls[0]?.[0]).toBe('/api/invoices/{invoice_id}')
    expect(sent()).toEqual({
      data_emissione: '2026-09-01',
      competenza_da: '2026-07-01',
      competenza_a: '2026-07-31',
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Date salvate'))
  })

  it('never sends a date for a fattura, whose date is not this form’s to set', async () => {
    wrap(<InvoiceHeaderEditor invoice={DRAFT} />)
    setDate('Competenza dal', '2026-08-01')
    setDate('Competenza al', '2026-08-31')
    await userEvent.click(screen.getByRole('button', { name: 'Salva date' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(sent()).toEqual({ competenza_da: '2026-08-01', competenza_a: '2026-08-31' })
  })

  it('sends an explicit null when the period is cleared, so the server clears it too', async () => {
    // A PATCH with the keys omitted would leave the old period in place while the
    // inputs read empty: the same defect `InvoiceLinesEditor` maps `''` to `null` for.
    wrap(<InvoiceHeaderEditor invoice={PROFORMA} />)
    setDate('Competenza dal', '')
    setDate('Competenza al', '')
    await userEvent.click(screen.getByRole('button', { name: 'Salva date' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(sent()).toMatchObject({ competenza_da: null, competenza_a: null })
  })

  it('refuses a lone end before asking the server', async () => {
    wrap(<InvoiceHeaderEditor invoice={DRAFT} />)
    setDate('Competenza dal', '2026-08-01')
    await userEvent.click(screen.getByRole('button', { name: 'Salva date' }))

    expect(
      screen.getByText('Il periodo di competenza richiede sia l’inizio sia la fine.'),
    ).toBeInTheDocument()
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('refuses a proforma without a date', async () => {
    wrap(<InvoiceHeaderEditor invoice={PROFORMA} />)
    setDate('Data', '')
    await userEvent.click(screen.getByRole('button', { name: 'Salva date' }))

    expect(screen.getByText('La proforma ha bisogno di una data.')).toBeInTheDocument()
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('shows the server’s own refusal when the document is frozen', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(
      failed(
        {
          type: 'https://pigrocrm.dev/errors/conflict',
          title: 'Conflitto',
          status: 409,
          detail: 'la proforma è già stata consumata',
          code: 'conflict',
        },
        409,
      ),
    )
    wrap(<InvoiceHeaderEditor invoice={PROFORMA} />)
    await userEvent.click(screen.getByRole('button', { name: 'Salva date' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('la proforma è già stata consumata')
    expect(toast.success).not.toHaveBeenCalled()
  })
})
