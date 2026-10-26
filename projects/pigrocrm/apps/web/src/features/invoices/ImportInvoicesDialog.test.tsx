/**
 * The invoices page's import (REB-758), driven the way a person drives it: pick files,
 * read the review, decide on the new customer, confirm, read the result. `fetch` is the
 * seam (the two routes are multipart, outside `openapi-fetch`), so what is asserted is
 * what the page really sends: the files, in batches, and the decisions keyed by hash.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConfirmRow, ReviewRow } from './importPlan'
import { ImportInvoicesButton } from './ImportInvoicesDialog'

const mockAuth = vi.hoisted(() => ({ may: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => mockAuth.may }))

const REVIEW = '/api/invoices/import/upload/review'
const CONFIRM = '/api/invoices/import/upload/confirm'

const mockFetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>()

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'content-type': 'application/json' },
    }),
  )
}

beforeEach(() => {
  mockAuth.may = true
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function invoice(numero: string, overrides: Record<string, unknown> = {}) {
  return {
    numero,
    data_emissione: '2026-03-15',
    tipo_documento: 'fattura',
    divisa: 'EUR',
    fornitore: { ragione_sociale: 'Chiara Bianchi', partita_iva: 'IT01234567890' },
    cliente: {
      ragione_sociale: 'Esempio Servizi S.r.l.',
      partita_iva: 'IT09876543210',
      indirizzo: 'Piazza dei Modelli 4',
      cap: '00187',
      comune: 'Roma',
      nazione: 'IT',
    },
    righe: [],
    riepiloghi: [],
    imponibile: '1000.00',
    imposta: '0.00',
    totale: '1000.00',
    cassa_previdenziale: [],
    termini_pagamento: [],
    trasmissione: {},
    ...overrides,
  }
}

function reviewRow(file: string, outcome: ReviewRow['outcome'], numero = '2026/6'): ReviewRow {
  return {
    file,
    sha256: `sha-${file}`,
    outcome,
    invoice: outcome === 'unclaimed' ? null : (invoice(numero) as never),
    party_key: outcome === 'unclaimed' ? null : '09876543210',
  }
}

function xml(name: string): File {
  return new File(['<xml/>'], name, { type: 'application/xml' })
}

function formOf(call: [string, RequestInit?] | undefined): FormData {
  return call?.[1]?.body as FormData
}

async function openAndPick(files: File[]) {
  const user = userEvent.setup()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ImportInvoicesButton />
    </QueryClientProvider>,
  )
  await user.click(screen.getByRole('button', { name: 'Importa' }))
  await user.upload(screen.getByLabelText('Carica i file delle fatture'), files)
  return user
}

describe('the import dialog', () => {
  it('reads the files and says what would be created before anything is written', async () => {
    mockFetch.mockImplementation((url) =>
      url === REVIEW
        ? json({
            righe: [
              reviewRow('uno.xml', 'ready', '2026/1'),
              reviewRow('due.xml', 'already_present', '2026/2'),
              reviewRow('note.txt', 'unclaimed'),
            ],
          })
        : json({}, 500),
    )

    await openAndPick([xml('uno.xml'), xml('due.xml'), xml('note.txt')])

    // One request, carrying the three files under the repeated `file` field.
    await screen.findByRole('button', { name: 'Importa 1 fattura' })
    expect(mockFetch).toHaveBeenCalledTimes(1)
    expect(formOf(mockFetch.mock.calls[0]).getAll('file')).toHaveLength(3)
    // The unreadable file is first, with its reason; what will simply happen is last.
    const table = screen.getByRole('table')
    const [, first, second, third] = within(table).getAllByRole('row')
    expect(first).toHaveTextContent('Non riconosciuto')
    expect(first).toHaveTextContent('Non è una fattura FatturaPA FPR12.')
    expect(second).toHaveTextContent('Già presente')
    expect(third).toHaveTextContent('2026/1')
    expect(third).toHaveTextContent('Da importare')
    expect(third).toHaveTextContent('1.000,00')
    // Reading is not writing.
    expect(mockFetch.mock.calls.every(([url]) => url === REVIEW)).toBe(true)
  })

  it('asks once per new customer, and an unticked customer is not created nor imported', async () => {
    mockFetch.mockImplementation((url) =>
      url === REVIEW
        ? json({
            righe: [
              reviewRow('a.xml', 'needs_customer_confirmation', '2026/1'),
              reviewRow('b.xml', 'needs_customer_confirmation', '2026/2'),
            ],
          })
        : json({}, 500),
    )

    const user = await openAndPick([xml('a.xml'), xml('b.xml')])

    // Two files, one customer: one checkbox, ticked, and both invoices counted.
    const section = await screen.findByRole('region', { name: 'Clienti da creare' })
    const checkbox = within(section).getByRole('checkbox')
    expect(checkbox).toBeChecked()
    expect(within(section).getByText(/2 fatture/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Importa 2 fatture' })).toBeEnabled()

    await user.click(checkbox)

    expect(screen.getByRole('button', { name: 'Niente da importare' })).toBeDisabled()
    expect(screen.getAllByText(/non viene importata/i)).toHaveLength(2)
  })

  it('confirms with the same files and the decision keyed by their hash, then reads the register', async () => {
    mockFetch.mockImplementation((url) => {
      if (url === REVIEW) {
        return json({ righe: [reviewRow('a.xml', 'needs_customer_confirmation', '2026/1')] })
      }
      const row: ConfirmRow = {
        file: 'a.xml',
        sha256: 'sha-a.xml',
        outcome: 'imported',
        azienda_id: 'az-1',
        fattura: { anno: 2026, numero: 1 } as ConfirmRow['fattura'],
        buchi_non_dichiarati: [],
      }
      return json({ righe: [row] })
    })

    const user = await openAndPick([xml('a.xml')])
    await user.click(await screen.findByRole('button', { name: 'Importa 1 fattura' }))

    await screen.findByText(/non manca nessun numero/)
    const confirm = mockFetch.mock.calls.find(([url]) => url === CONFIRM)
    expect(formOf(confirm).getAll('file')).toHaveLength(1)
    expect(JSON.parse(formOf(confirm).get('decisioni') as string)).toEqual({
      clienti: { '09876543210': { create_customer: true } },
    })
    expect(screen.getByText('Registro 2026:')).toBeInTheDocument()
    expect(screen.getByText('Importate')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Chiudi' }).at(-1)!)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('lists the numbers the register still lacks', async () => {
    mockFetch.mockImplementation((url) =>
      url === REVIEW
        ? json({ righe: [reviewRow('a.xml', 'ready', '2026/1')] })
        : json({
            righe: [
              {
                file: 'a.xml',
                sha256: 'sha-a.xml',
                outcome: 'imported',
                azienda_id: 'az-1',
                fattura: { anno: 2026, numero: 5 },
                buchi_non_dichiarati: [1, 2, 3, 4],
              },
            ],
          }),
    )

    const user = await openAndPick([xml('a.xml')])
    await user.click(await screen.findByRole('button', { name: 'Importa 1 fattura' }))

    await screen.findByText(/mancano ancora i numeri 1, 2, 3, 4/)
  })

  it('sends a year in batches and writes only the batches that hold something', async () => {
    const files = Array.from({ length: 30 }, (_, i) => xml(`f${String(i).padStart(2, '0')}.xml`))
    mockFetch.mockImplementation((url, init) => {
      const names = (init?.body as FormData).getAll('file').map((f) => (f as File).name)
      if (url === REVIEW) {
        // The first 25 files are already on the register, the last 5 are new.
        return json({
          righe: names.map((n) => reviewRow(n, n < 'f25' ? 'already_present' : 'ready', n)),
        })
      }
      return json({
        righe: names.map((n) => ({
          file: n,
          sha256: `sha-${n}`,
          outcome: 'imported',
          azienda_id: 'az-1',
          fattura: { anno: 2026, numero: 1 },
          buchi_non_dichiarati: [],
        })),
      })
    })

    const user = await openAndPick(files)
    await user.click(await screen.findByRole('button', { name: 'Importa 5 fatture' }))

    await screen.findByText(/non manca nessun numero/)
    const reviews = mockFetch.mock.calls.filter(([url]) => url === REVIEW)
    const confirms = mockFetch.mock.calls.filter(([url]) => url === CONFIRM)
    expect(reviews.map((call) => formOf(call).getAll('file').length)).toEqual([25, 5])
    // Only the batch with something to write is sent again.
    expect(confirms.map((call) => formOf(call).getAll('file').length)).toEqual([5])
  })

  it('shows the server’s own sentence when a ZIP does not open, and lets the person choose again', async () => {
    mockFetch.mockImplementation(() =>
      json(
        {
          type: 'about:blank',
          title: 'Validazione',
          status: 422,
          code: 'validation_failed',
          detail: 'invoice.files: rotto.zip: l’archivio ZIP non si legge',
        },
        422,
      ),
    )

    await openAndPick([new File(['PK'], 'rotto.zip', { type: 'application/zip' })])

    expect(await screen.findByRole('alert')).toHaveTextContent('rotto.zip')
    expect(screen.getByLabelText('Carica i file delle fatture')).toBeInTheDocument()
  })

  it('reports what was written when a later batch fails, instead of going back to a stale review', async () => {
    const files = Array.from({ length: 30 }, (_, i) => xml(`f${String(i).padStart(2, '0')}.xml`))
    let confirms = 0
    mockFetch.mockImplementation((url, init) => {
      const names = (init?.body as FormData).getAll('file').map((f) => (f as File).name)
      if (url === REVIEW) return json({ righe: names.map((n) => reviewRow(n, 'ready', n)) })
      confirms += 1
      if (confirms === 2) {
        return json({ type: 'about:blank', title: 'Errore', status: 500, code: 'x', detail: 'Il server non risponde.' }, 500)
      }
      return json({
        righe: names.map((n) => ({
          file: n,
          sha256: `sha-${n}`,
          outcome: 'imported',
          azienda_id: 'az-1',
          fattura: { anno: 2026, numero: 1 },
          buchi_non_dichiarati: [9],
        })),
      })
    })

    const user = await openAndPick(files)
    await user.click(await screen.findByRole('button', { name: 'Importa 30 fatture' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    // «Chiudi» twice, the corner's and the footer's: both close, so either proves the stage.
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Chiudi' })).toHaveLength(2))
    expect(screen.getByText('25')).toBeInTheDocument()
    expect(screen.getByText(/mancano ancora i numeri 9/)).toBeInTheDocument()
  })

  it('attaches a customer the first batch created by id in the next one, not created twice', async () => {
    const files = Array.from({ length: 30 }, (_, i) => xml(`f${String(i).padStart(2, '0')}.xml`))
    mockFetch.mockImplementation((url, init) => {
      const names = (init?.body as FormData).getAll('file').map((f) => (f as File).name)
      if (url === REVIEW) {
        return json({ righe: names.map((n) => reviewRow(n, 'needs_customer_confirmation', n)) })
      }
      return json({
        righe: names.map((n) => ({
          file: n,
          sha256: `sha-${n}`,
          outcome: 'imported',
          party_key: '09876543210',
          azienda_id: 'az-1',
          fattura: { anno: 2026, numero: 1, customer_id: 'customer-1' },
          buchi_non_dichiarati: [],
        })),
      })
    })

    const user = await openAndPick(files)
    await user.click(await screen.findByRole('button', { name: 'Importa 30 fatture' }))

    await screen.findByText(/non manca nessun numero/)
    const decisions = mockFetch.mock.calls
      .filter(([url]) => url === CONFIRM)
      .map((call) => JSON.parse(formOf(call).get('decisioni') as string))
    expect(decisions).toEqual([
      { clienti: { '09876543210': { create_customer: true } } },
      { clienti: { '09876543210': { customer_id: 'customer-1' } } },
    ])
  })

  it('names the file of every invoice the register refused, and why a file did not read', async () => {
    mockFetch.mockImplementation((url) =>
      url === REVIEW
        ? json({
            righe: [
              reviewRow('uno.xml', 'ready', '2026/1'),
              { ...reviewRow('rotta.xml', 'unclaimed'), motivo: "unrecognised TipoDocumento: 'TD99'" },
            ],
          })
        : json({
            righe: [
              { file: 'uno.xml', sha256: 'sha-uno.xml', outcome: 'conflict', azienda_id: 'az-1' },
              { file: 'rotta.xml', sha256: 'sha-rotta.xml', outcome: 'unclaimed', motivo: 'TD99' },
            ],
          }),
    )

    const user = await openAndPick([xml('uno.xml'), xml('rotta.xml')])
    expect(await screen.findByText(/non si legge: unrecognised TipoDocumento/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Importa 1 fattura' }))

    const section = await screen.findByRole('region', { name: 'Da guardare' })
    expect(within(section).getByText('uno.xml')).toBeInTheDocument()
    expect(within(section).getByText(/rotta.xml/)).toBeInTheDocument()
    expect(within(section).getByText(/Non riconosciute: TD99/)).toBeInTheDocument()
  })

  it('does not claim nothing was imported when the first batch fails', async () => {
    mockFetch.mockImplementation((url) =>
      url === REVIEW
        ? json({ righe: [reviewRow('uno.xml', 'ready', '2026/1')] })
        : json({ type: 'about:blank', title: 'Errore', status: 500, code: 'x', detail: 'Il server non risponde.' }, 500),
    )

    const user = await openAndPick([xml('uno.xml')])
    await user.click(await screen.findByRole('button', { name: 'Importa 1 fattura' }))

    expect(await screen.findByText(/L’importazione si è interrotta/)).toBeInTheDocument()
    expect(screen.queryByText(/Nessuna fattura è stata importata/)).not.toBeInTheDocument()
  })

  it('is not offered to a role the service refuses', () => {
    mockAuth.may = false
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ImportInvoicesButton />
      </QueryClientProvider>,
    )

    expect(screen.queryByRole('button', { name: 'Importa' })).not.toBeInTheDocument()
  })
})
