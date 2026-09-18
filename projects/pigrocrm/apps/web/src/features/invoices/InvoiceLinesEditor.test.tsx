import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InvoiceLinesEditor } from './InvoiceLinesEditor'
import { api } from '@/lib/api'
import type { Invoice, InvoiceLine } from './queries'

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

const DRAFT = { id: 'inv-1', tipo: 'fattura', stato: 'bozza' } as unknown as Invoice

const LINE: InvoiceLine = {
  id: 'line-1',
  invoice_id: 'inv-1',
  numero_linea: 1,
  descrizione: 'Consulenza',
  quantita: '3.000000',
  unita_misura: 'ore',
  prezzo_unitario: '500.000000',
  sconto_percentuale: null,
  sconto_importo: null,
  prezzo_totale: '1500.00',
  aliquota_iva: '0.00',
  natura: 'N2.2',
  riferimento_normativo: 'art. 1',
}

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

beforeEach(() => {
  vi.mocked(api.PUT).mockResolvedValue({
    data: { id: 'inv-1', totale: '1500.00' },
    response: new Response(null, { status: 200 }),
  } as never)
})

afterEach(() => {
  vi.mocked(api.PUT).mockReset()
})

/** The `righe` array as it reached `api.PUT`. Asserting on what we send beats
 *  asserting on a serialised HTTP body: it is the same fact, one layer earlier, and it
 *  survives a change of transport. */
function sentLines(): { sconto_percentuale: string | null; unita_misura: string | null }[] {
  const [, options] = vi.mocked(api.PUT).mock.calls[0] as unknown as [
    string,
    { body: { righe: { sconto_percentuale: string | null; unita_misura: string | null }[] } },
  ]
  return options.body.righe
}

describe('InvoiceLinesEditor', () => {
  it('shows the stored line total, never a recomputed one', () => {
    // The authoritative total is the API's. The running figure below the table is
    // explicitly labelled as a preview so nobody reads it as the document's total.
    wrap(<InvoiceLinesEditor invoice={DRAFT} lines={[LINE]} readOnly={false} />)
    expect(screen.getByDisplayValue('Consulenza')).toBeInTheDocument()
    expect(screen.getByText(/1\.500,00/)).toBeInTheDocument()
  })

  it('adds and removes rows', async () => {
    const user = userEvent.setup()
    wrap(<InvoiceLinesEditor invoice={DRAFT} lines={[LINE]} readOnly={false} />)
    await user.click(screen.getByRole('button', { name: /aggiungi riga/i }))
    expect(screen.getAllByLabelText(/descrizione/i)).toHaveLength(2)
    await user.click(screen.getAllByRole('button', { name: /rimuovi riga/i })[1] as HTMLElement)
    expect(screen.getAllByLabelText(/descrizione/i)).toHaveLength(1)
  })

  it('treats a zero discount as a value, not a blank', async () => {
    // `0` and `false` are values, never blanks -- mirroring `is_blank`. A zero
    // percentage discount must survive the round trip as `0`, not vanish.
    const user = userEvent.setup()
    wrap(<InvoiceLinesEditor invoice={DRAFT} lines={[LINE]} readOnly={false} />)
    const discount = screen.getByLabelText(/sconto %/i)
    await user.clear(discount)
    await user.type(discount, '0')
    await user.click(screen.getByRole('button', { name: /salva righe/i }))
    // Awaited: the mutation is asynchronous, so reading fetch.mock straight after
    // the click sees no call at all. The brief's version raced and reported
    // '"undefined" is not valid JSON', which reads like a body-shape problem.
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(sentLines()[0]?.sconto_percentuale).toBe('0')
  })

  it('sends an emptied optional field as null so the bulk replace clears it', async () => {
    const user = userEvent.setup()
    wrap(<InvoiceLinesEditor invoice={DRAFT} lines={[LINE]} readOnly={false} />)
    await user.clear(screen.getByLabelText(/unità/i))
    await user.click(screen.getByRole('button', { name: /salva righe/i }))
    // Awaited: the mutation is asynchronous, so reading fetch.mock straight after
    // the click sees no call at all. The brief's version raced and reported
    // '"undefined" is not valid JSON', which reads like a body-shape problem.
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(sentLines()[0]?.unita_misura).toBeNull()
  })

  it('is read-only for an issued invoice, with no save button at all', () => {
    const issued = { ...DRAFT, stato: 'emessa' } as unknown as Invoice
    wrap(<InvoiceLinesEditor invoice={issued} lines={[LINE]} readOnly />)
    expect(screen.queryByRole('button', { name: /salva righe/i })).not.toBeInTheDocument()
    expect(screen.getByText('Consulenza')).toBeInTheDocument()
  })

  it('does not offer a VAT rate field, because the regime decides it', () => {
    // Accepting a rate here would put the table constraint
    // `(aliquota_iva = 0) = (natura IS NOT NULL)` within reach of a form.
    wrap(<InvoiceLinesEditor invoice={DRAFT} lines={[LINE]} readOnly={false} />)
    expect(screen.queryByLabelText(/aliquota/i)).not.toBeInTheDocument()
  })
})
