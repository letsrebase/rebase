import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InvoiceActions } from './InvoiceActions'
import { InvoiceStateBadge } from './InvoiceStateBadge'
import type { Invoice } from './queries'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
  }
})
vi.mock('@rebase/ui/sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))
// REB-294: every button on this bar reads its own action through `useCan` (one check
// per button, against the service's own `require_write`/`require_admin` string). The
// suite's default actor is one the table answers true for, so an admin sees every
// button; the readonly side flips the same switch below.
const mockAuth = vi.hoisted(() => ({ may: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => mockAuth.may }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const DRAFT = { id: 'inv-1', tipo: 'fattura', stato: 'bozza' } as unknown as Invoice
const ISSUED = {
  id: 'inv-1',
  tipo: 'fattura',
  stato: 'emessa',
  stato_pagamento: 'da_incassare',
  trasmessa_esternamente_il: null,
} as unknown as Invoice
const COLLECTED = { ...ISSUED, stato_pagamento: 'incassato', data_incasso: '2026-09-01' } as Invoice
// REB-168: the download buttons follow the row's document ids, so a fixture that means
// "an issued invoice with its files" has to say so.
const RENDERED = { ...ISSUED, pdf_document_id: 'doc-pdf', xml_document_id: 'doc-xml' } as Invoice
const PROFORMA = { id: 'pf-1', tipo: 'proforma', stato: 'confermata' } as unknown as Invoice
const DRAFT_PROFORMA = { id: 'pf-1', tipo: 'proforma', stato: 'bozza' } as unknown as Invoice

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

beforeEach(() => {
  mockAuth.may = true
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.warning).mockReset()
  vi.mocked(toast.error).mockReset()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('InvoiceActions', () => {
  it('asks before issuing, because the number cannot be taken back', async () => {
    vi.mocked(api.POST).mockResolvedValue(ok(ISSUED))
    wrap(<InvoiceActions invoice={DRAFT} />)

    await userEvent.click(screen.getByRole('button', { name: /emetti/i }))
    expect(window.confirm).toHaveBeenCalled()
  })

  it('does not issue when the confirmation is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    wrap(<InvoiceActions invoice={DRAFT} />)

    await userEvent.click(screen.getByRole('button', { name: /emetti/i }))
    expect(api.POST).not.toHaveBeenCalled()
  })

  /**
   * REB-143: the endpoint renders on emission and answers the row read back after it, so
   * a row that already carries both files needs no second render. Two calls here used
   * to compile the same PDF twice on every emission.
   */
  it('does not render again when the issued row already carries both files', async () => {
    const rendered = { ...ISSUED, pdf_document_id: 'doc-pdf', xml_document_id: 'doc-xml' }
    vi.mocked(api.POST).mockResolvedValue(ok(rendered))
    const onIssued = vi.fn()
    wrap(<InvoiceActions invoice={DRAFT} onIssued={onIssued} />)

    await userEvent.click(screen.getByRole('button', { name: /emetti/i }))

    await waitFor(() => expect(onIssued).toHaveBeenCalledWith(rendered))
    expect(api.POST).toHaveBeenCalledTimes(1)
    expect(String(vi.mocked(api.POST).mock.calls[0]?.[0])).toContain('/issue')
    expect(toast.success).toHaveBeenCalled()
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('renders the artefacts again when the issued row is missing one, as a second call', async () => {
    // The half-way case the endpoint can answer: the PDF committed, the XML export failed.
    const halfRendered = { ...ISSUED, pdf_document_id: 'doc-pdf', xml_document_id: null }
    vi.mocked(api.POST).mockResolvedValue(ok(halfRendered))
    const onIssued = vi.fn()
    wrap(<InvoiceActions invoice={DRAFT} onIssued={onIssued} />)

    await userEvent.click(screen.getByRole('button', { name: /emetti/i }))
    await waitFor(() => expect(vi.mocked(api.POST).mock.calls.length).toBe(2))
    // The mocked signature widens to `never`, so the tuple is read positionally
    // rather than destructured.
    const paths = vi
      .mocked(api.POST)
      .mock.calls.map((call) => String((call as unknown as unknown[])[0]))
    expect(paths[0]).toContain('/issue')
    expect(paths[1]).toContain('/artifacts')
    // A draft fattura is issued in place: the caller gets the same id back and has
    // nothing to navigate to. The route relies on this to stay put (ORB-134).
    expect(onIssued).toHaveBeenCalledWith(halfRendered)
    expect(onIssued.mock.calls[0]?.[0]?.id).toBe(DRAFT.id)
  })

  /**
   * The route navigates away in `onIssued`, so this bar unmounts while the render is
   * still in flight. TanStack Query drops the callbacks passed to `mutate()` once the
   * observer has no listeners, which silently lost the warning on exactly the flow
   * ORB-134 introduces; the promise form does not. Pinned by unmounting from the
   * callback, as the route does.
   */
  it('still warns about a failed render after the caller has navigated away', async () => {
    vi.mocked(api.POST)
      .mockResolvedValueOnce(ok({ ...ISSUED, id: 'inv-18' }))
      .mockResolvedValueOnce(failed({ detail: 'typst non disponibile' }, 500))
    const view = wrap(<InvoiceActions invoice={PROFORMA} onIssued={() => view.unmount()} />)

    await userEvent.click(screen.getByRole('button', { name: /emetti/i }))

    await waitFor(() => expect(toast.warning).toHaveBeenCalled())
    expect(String(vi.mocked(toast.warning).mock.calls[0]?.[0])).toContain('Rigenera documenti')
    expect(vi.mocked(toast.error)).not.toHaveBeenCalled()
  })

  /**
   * The one that matters. `issue()` is one transaction and does not produce the
   * artefacts — the caller does — so a failure of that second call leaves an invoice
   * that has its number and is fiscally complete, merely unprinted. Reporting "emission
   * failed" would be the more dangerous lie: it would send someone to reissue a
   * document that already exists in the register.
   */
  it('does not call a failed render a failed emission', async () => {
    vi.mocked(api.POST)
      .mockResolvedValueOnce(ok(ISSUED))
      .mockResolvedValueOnce(failed({ detail: 'typst non disponibile' }, 500))
    wrap(<InvoiceActions invoice={DRAFT} />)

    await userEvent.click(screen.getByRole('button', { name: /emetti/i }))

    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(String(vi.mocked(toast.success).mock.calls[0]?.[0])).toContain('emessa')
    await waitFor(() => expect(toast.warning).toHaveBeenCalled())
    const warning = String(vi.mocked(toast.warning).mock.calls[0]?.[0])
    expect(warning).toContain('emesso correttamente')
    expect(warning).toContain('Rigenera documenti')
    expect(vi.mocked(toast.error)).not.toHaveBeenCalled()
  })

  /**
   * From a proforma the numbered row is a *new* one (spec 5). Until this existed the
   * bar rendered the proforma's own PDF, toasted, and stayed on a page that had just
   * become a consumed proforma with nothing left to do on it; the fattura and its XML
   * were somewhere in the list (ORB-134). Now the render targets the issued row, the
   * toast names the number, and the caller is handed the row to go to.
   */
  it('renders the issued row, not the proforma, and hands it to the caller', async () => {
    const issuedFromProforma = {
      ...ISSUED,
      id: 'inv-18',
      anno: 2026,
      numero: 18,
      origine_proforma_id: 'pf-1',
    } as unknown as Invoice
    vi.mocked(api.POST).mockResolvedValueOnce(ok(issuedFromProforma)).mockResolvedValueOnce(ok([]))
    const onIssued = vi.fn()
    wrap(<InvoiceActions invoice={PROFORMA} onIssued={onIssued} />)

    await userEvent.click(screen.getByRole('button', { name: /emetti/i }))

    await waitFor(() => expect(vi.mocked(api.POST).mock.calls.length).toBe(2))
    const paths = vi.mocked(api.POST).mock.calls.map((call) => String(call[0]))
    expect(paths[0]).toContain('/issue')
    expect(paths[1]).toContain('/artifacts')
    const render = vi.mocked(api.POST).mock.calls[1] as unknown as [string, { params: unknown }]
    expect(render[1].params).toEqual({ path: { invoice_id: 'inv-18' } })
    expect(onIssued).toHaveBeenCalledWith(issuedFromProforma)
    expect(String(vi.mocked(toast.success).mock.calls[0]?.[0])).toBe('Fattura 2026/18 emessa')
  })

  // --- the step before emission: confirming a proforma (ORB-132) -------------------------

  /** Until this existed a proforma created from the web could never be issued from the
   *  web: «Emetti» waits for a confirmed proforma and nothing confirmed one. */
  it('offers «Conferma» on a draft proforma, where «Emetti» will appear once it is confirmed', () => {
    wrap(<InvoiceActions invoice={DRAFT_PROFORMA} />)
    expect(screen.getByRole('button', { name: /^conferma$/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /emetti/i })).toBeNull()
  })

  it('confirms through the confirm endpoint and says so, with no number consumed', async () => {
    vi.mocked(api.POST).mockResolvedValue(ok(PROFORMA))
    wrap(<InvoiceActions invoice={DRAFT_PROFORMA} />)
    await userEvent.click(screen.getByRole('button', { name: /^conferma$/i }))
    await waitFor(() => expect(vi.mocked(api.POST)).toHaveBeenCalledTimes(1))
    const path = String((vi.mocked(api.POST).mock.calls[0] as unknown as unknown[])[0])
    expect(path).toContain('/confirm')
    expect(path).not.toContain('/issue')
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Proforma confermata'))
    // No confirmation dialog: unlike emission, this consumes nothing and forecloses
    // nothing, since a confirmed proforma stays editable and deletable.
    expect(window.confirm).not.toHaveBeenCalled()
  })

  it('shows the server refusal when a proforma without lines is confirmed', async () => {
    vi.mocked(api.POST).mockResolvedValue(
      failed({ detail: 'una proforma senza righe non si conferma' }, 422),
    )
    wrap(<InvoiceActions invoice={DRAFT_PROFORMA} />)
    await userEvent.click(screen.getByRole('button', { name: /^conferma$/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/senza righe/))
  })

  it('offers no «Conferma» on a confirmed proforma or on a fattura', () => {
    wrap(<InvoiceActions invoice={PROFORMA} />)
    expect(screen.queryByRole('button', { name: /^conferma$/i })).toBeNull()
    expect(screen.getByRole('button', { name: /emetti/i })).toBeInTheDocument()
    wrap(<InvoiceActions invoice={DRAFT} />)
    expect(screen.queryByRole('button', { name: /^conferma$/i })).toBeNull()
  })

  it('offers no issue button once the invoice is issued', () => {
    wrap(<InvoiceActions invoice={ISSUED} />)
    expect(screen.queryByRole('button', { name: /emetti/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /annulla/i })).toBeInTheDocument()
  })

  it('says annulment keeps the number, rather than offering a delete', async () => {
    wrap(<InvoiceActions invoice={ISSUED} />)
    expect(screen.queryByRole('button', { name: /elimina/i })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^annulla$/i }))
    expect(screen.getByText(/Il numero resta nel registro/)).toBeInTheDocument()
  })

  it('offers to produce a proforma PDF that does not exist yet, and to download one that does', async () => {
    vi.mocked(api.POST).mockResolvedValue(ok([]))
    const { unmount } = wrap(
      <InvoiceActions invoice={{ ...PROFORMA, pdf_document_id: null } as Invoice} />,
    )
    expect(screen.queryByRole('button', { name: /^pdf proforma$/i })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /genera pdf proforma/i }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/invoices/{invoice_id}/artifacts', {
        params: { path: { invoice_id: 'pf-1' } },
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('PDF proforma generato')
    unmount()
    wrap(<InvoiceActions invoice={{ ...PROFORMA, pdf_document_id: 'doc-1' } as Invoice} />)
    expect(screen.getByRole('button', { name: /^pdf proforma$/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /genera pdf proforma/i })).not.toBeInTheDocument()
  })

  // --- deleting what never had a number (slice 3 §4) ---------------------------------------

  it('deletes a draft after a confirmation, then hands the page back to its caller', async () => {
    vi.mocked(api.DELETE).mockResolvedValue(ok(undefined))
    const onDeleted = vi.fn()
    wrap(<InvoiceActions invoice={DRAFT} onDeleted={onDeleted} />)
    await userEvent.click(screen.getByRole('button', { name: /elimina bozza/i }))
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/Eliminare questa bozza/))
    await waitFor(() =>
      expect(api.DELETE).toHaveBeenCalledWith('/api/invoices/{invoice_id}', {
        params: { path: { invoice_id: 'inv-1' } },
      }),
    )
    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1))
    expect(toast.success).toHaveBeenCalledWith('Bozza eliminata')
  })

  it('does not delete when the confirmation is declined', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    const onDeleted = vi.fn()
    wrap(<InvoiceActions invoice={DRAFT} onDeleted={onDeleted} />)
    await userEvent.click(screen.getByRole('button', { name: /elimina bozza/i }))
    expect(api.DELETE).not.toHaveBeenCalled()
    expect(onDeleted).not.toHaveBeenCalled()
  })

  it.each([['bozza'], ['confermata']])(
    'offers to delete a %s proforma, and names it as such',
    async (stato) => {
      vi.mocked(api.DELETE).mockResolvedValue(ok(undefined))
      wrap(<InvoiceActions invoice={{ ...PROFORMA, stato } as Invoice} />)
      await userEvent.click(screen.getByRole('button', { name: /elimina proforma/i }))
      expect(window.confirm).toHaveBeenCalledWith(
        expect.stringMatching(/Eliminare questa proforma/),
      )
      await waitFor(() => expect(api.DELETE).toHaveBeenCalledTimes(1))
      expect(toast.success).toHaveBeenCalledWith('Proforma eliminata')
    },
  )

  it('offers no delete on a consumed proforma: it is the antecedent of a numbered document', () => {
    wrap(<InvoiceActions invoice={{ ...PROFORMA, stato: 'consumata' } as Invoice} />)
    expect(screen.queryByRole('button', { name: /elimina/i })).not.toBeInTheDocument()
  })

  it('shows the server refusal and stays on the page when the delete is a 409', async () => {
    vi.mocked(api.DELETE).mockResolvedValue(
      failed({ detail: 'il documento e\u2019 stato emesso nel frattempo e non si elimina piu\u2019' }, 409),
    )
    const onDeleted = vi.fn()
    wrap(<InvoiceActions invoice={DRAFT} onDeleted={onDeleted} />)
    await userEvent.click(screen.getByRole('button', { name: /elimina bozza/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/non si elimina/))
    expect(onDeleted).not.toHaveBeenCalled()
  })

  // --- the states after emission: collected, transmitted ---------------------------------

  it('records a collection with its date, through the payment endpoint', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(ok(COLLECTED))
    wrap(<InvoiceActions invoice={ISSUED} />)

    await userEvent.click(screen.getByRole('button', { name: /segna incassata/i }))
    const dialog = screen.getByRole('dialog', { name: /registra l'incasso/i })
    // Today, proposed rather than imposed: the field is editable and the server needs a
    // date, so the dialog never sends a collection without one.
    const date = screen.getByLabelText('Data incasso') as HTMLInputElement
    expect(date.value).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    fireEvent.change(date, { target: { value: '2026-09-01' } })
    await userEvent.click(within(dialog).getByRole('button', { name: /registra incasso/i }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalledTimes(1))
    const [path, options] = vi.mocked(api.PATCH).mock.calls[0] as unknown as [
      string,
      { body: unknown },
    ]
    expect(path).toContain('/payment')
    expect(options.body).toEqual({ stato_pagamento: 'incassato', data_incasso: '2026-09-01' })

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Incasso registrato'))
  })

  it('offers the way back once collected, clearing the date with the state', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(ok(ISSUED))
    wrap(<InvoiceActions invoice={COLLECTED} />)

    expect(screen.queryByRole('button', { name: /segna incassata/i })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /segna da incassare/i }))
    expect(window.confirm).toHaveBeenCalled()

    await waitFor(() => expect(api.PATCH).toHaveBeenCalledTimes(1))
    const [, options] = vi.mocked(api.PATCH).mock.calls[0] as unknown as [string, { body: unknown }]
    expect(options.body).toEqual({ stato_pagamento: 'da_incassare', data_incasso: null })
  })

  it('records the external transmission once, and never offers it again', async () => {
    vi.mocked(api.POST).mockResolvedValue(ok({ ...ISSUED, trasmessa_esternamente_il: '2026-09-02' }))
    wrap(<InvoiceActions invoice={ISSUED} />)

    await userEvent.click(screen.getByRole('button', { name: /segna trasmessa/i }))
    const dialog = screen.getByRole('dialog', { name: /segna come trasmessa/i })
    expect(dialog).toHaveTextContent(/Non si può annullare/)
    fireEvent.change(screen.getByLabelText('Data di trasmissione'), {
      target: { value: '2026-09-02' },
    })
    await userEvent.click(within(dialog).getByRole('button', { name: /segna trasmessa/i }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    const [path, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: unknown },
    ]
    expect(path).toContain('/transmitted')
    expect(options.body).toEqual({ data: '2026-09-02' })

  })

  it('never offers the transmission again once it is recorded', () => {
    // The column is frozen on the server (`mark_transmitted_externally`), so the button
    // follows the row and is gone.
    wrap(<InvoiceActions invoice={{ ...ISSUED, trasmessa_esternamente_il: '2026-09-02' } as Invoice} />)
    expect(screen.queryByRole('button', { name: /segna trasmessa/i })).toBeNull()
  })

  it('offers no payment or transmission action on a draft, a proforma or an imported invoice', () => {
    wrap(<InvoiceActions invoice={DRAFT} />)
    expect(screen.queryByRole('button', { name: /segna/i })).toBeNull()

    wrap(<InvoiceActions invoice={PROFORMA} />)
    expect(screen.queryByRole('button', { name: /segna/i })).toBeNull()

    // Imported: collected here, yes -- the money is still owed to the titolare -- but
    // transmitted by the system that issued it, so only the payment button remains.
    wrap(<InvoiceActions invoice={{ ...ISSUED, importata_da: 'esterno' } as Invoice} />)
    expect(screen.getByRole('button', { name: /segna incassata/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /segna trasmessa/i })).toBeNull()
  })

  it('hides the XML and regenerate actions for an imported invoice', () => {
    // With an XML id on the row, so it is the import that hides the button and not the
    // missing file.
    const imported = { ...RENDERED, importata_da: 'esterno' } as Invoice
    wrap(<InvoiceActions invoice={imported} />)
    expect(screen.queryByRole('button', { name: /XML FatturaPA/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Rigenera/i })).toBeNull()
    // The PDF stays: for an imported invoice it is the original document, not one
    // pigroCRM produced, so there is nothing to regenerate but nothing to hide either.
    expect(screen.getByRole('button', { name: /^PDF$/i })).toBeInTheDocument()
    // And no note about a missing XML: an imported invoice never had one rendered here,
    // and «Rigenera documenti» is not on its page to point at.
    expect(screen.queryByTestId('missing-invoice-files')).toBeNull()
  })

  // --- REB-168: a download exists only when its file does ---------------------------

  it('offers both downloads, and says nothing, when both files exist', () => {
    wrap(<InvoiceActions invoice={RENDERED} />)
    expect(screen.getByRole('button', { name: /^PDF$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /XML FatturaPA/i })).toBeInTheDocument()
    expect(screen.queryByTestId('missing-invoice-files')).toBeNull()
  })

  it('drops «XML FatturaPA» when the XML was never produced, and points at «Rigenera documenti»', () => {
    wrap(<InvoiceActions invoice={{ ...RENDERED, xml_document_id: null } as Invoice} />)
    expect(screen.queryByRole('button', { name: /XML FatturaPA/i })).toBeNull()
    expect(screen.getByRole('button', { name: /^PDF$/i })).toBeInTheDocument()
    expect(screen.getByTestId('missing-invoice-files')).toHaveTextContent(
      'L’XML FatturaPA di questa fattura non è stato generato. Premi «Rigenera documenti» per generarlo: il numero resta quello.',
    )
    expect(screen.getByRole('button', { name: /Rigenera documenti/i })).toBeInTheDocument()
  })

  it('drops both downloads when neither file exists, and says so once', () => {
    const unrendered = { ...RENDERED, pdf_document_id: null, xml_document_id: null } as Invoice
    wrap(<InvoiceActions invoice={unrendered} />)
    expect(screen.queryByRole('button', { name: /^PDF$/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /XML FatturaPA/i })).toBeNull()
    expect(screen.getByTestId('missing-invoice-files')).toHaveTextContent(
      'Il PDF e l’XML FatturaPA di questa fattura non sono stati generati. Premi «Rigenera documenti» per generarli',
    )
  })

  it('tells a readonly person where the file comes from rather than to press a button they lack', () => {
    mockAuth.may = false
    wrap(<InvoiceActions invoice={{ ...RENDERED, pdf_document_id: null } as Invoice} />)
    expect(screen.queryByRole('button', { name: /^PDF$/i })).toBeNull()
    expect(screen.getByRole('button', { name: /XML FatturaPA/i })).toBeInTheDocument()
    expect(screen.getByTestId('missing-invoice-files')).toHaveTextContent(
      'Il PDF di questa fattura non è stato generato. Si genera con «Rigenera documenti», che il tuo ruolo non può usare.',
    )
  })

  /** What Ivan saw on production (2026-09-11): the press answered the server's log line,
   *  `invoice_artifact <uuid>#xml not found`. A file that goes missing after the page
   *  loaded still 404s, and the toast says what that means in Italian. */
  it('turns a 404 on download into a sentence, never the raw identifier', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Not Found',
          status: 404,
          code: 'not_found',
          detail: 'invoice_artifact inv-1#xml not found',
          entity: 'invoice_artifact',
          identifier: 'inv-1#xml',
        }),
        { status: 404, headers: { 'content-type': 'application/problem+json' } },
      ),
    )
    wrap(<InvoiceActions invoice={RENDERED} />)

    await userEvent.click(screen.getByRole('button', { name: /XML FatturaPA/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    const message = String(vi.mocked(toast.error).mock.calls[0]?.[0])
    expect(message).toBe(
      'L’XML FatturaPA di questa fattura non è disponibile. Premi «Rigenera documenti» per generarlo di nuovo.',
    )
    expect(message).not.toContain('invoice_artifact')
  })

  it('drops the pointer from the 404 sentence for a person who has no «Rigenera documenti»', async () => {
    mockAuth.may = false
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Not Found',
          status: 404,
          code: 'not_found',
          detail: 'document_blob documents/doc-pdf/1 not found',
        }),
        { status: 404, headers: { 'content-type': 'application/problem+json' } },
      ),
    )
    wrap(<InvoiceActions invoice={RENDERED} />)

    await userEvent.click(screen.getByRole('button', { name: /^PDF$/i }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Il PDF di questa fattura non è disponibile.'),
    )
  })

  it('keeps the server’s own words for a download that fails any other way', async () => {
    const detail =
      "emitter_profile.codice_fiscale: il nome del file XML richiede un codice fiscale o una partita IVA validi dell'emittente"
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Validation Failed',
          status: 422,
          code: 'validation_failed',
          detail,
          entity: 'emitter_profile',
          field: 'codice_fiscale',
        }),
        { status: 422, headers: { 'content-type': 'application/problem+json' } },
      ),
    )
    wrap(<InvoiceActions invoice={RENDERED} />)

    await userEvent.click(screen.getByRole('button', { name: /XML FatturaPA/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detail))
  })

  /** REB-294: a readonly actor is offered nothing the service would answer 403 to.
   *  The downloads stay -- `InvoiceService.download` gates on nothing but the session
   *  -- and every button whose action the service gates is gone: no «Emetti», no
   *  «Annulla», no «Segna incassata», no «Rigenera documenti», and on a draft, nothing
   *  at all where «Conferma»/«Elimina bozza» used to stand. */
  it('shows a readonly actor only the downloads of an issued invoice', () => {
    mockAuth.may = false
    wrap(<InvoiceActions invoice={RENDERED} />)
    expect(screen.getByRole('button', { name: /^PDF$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /XML FatturaPA/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /segna/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /annulla/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /rigenera/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /emetti/i })).toBeNull()
  })

  it('shows a readonly actor no draft controls at all', () => {
    mockAuth.may = false
    wrap(<InvoiceActions invoice={DRAFT} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})

/** The badge lives in its own file (see there for why); its tests live here beside the
 *  actions that read the same `importata_da` flag. */
describe('InvoiceStateBadge', () => {
  it('shows the "imported" badge next to the state badge, naming no source', () => {
    const imported = { ...ISSUED, importata_da: 'esterno' } as Invoice
    wrap(<InvoiceStateBadge invoice={imported} />)
    expect(screen.getByText(/^importata$/i)).toBeInTheDocument()
  })

  /** The list turns the pill off (ORB-130). Rendered with no option, as the detail page
   *  does, the badge keeps it: that is the test above this one. */
  it('leaves the "imported" badge out when asked to, and keeps the state', () => {
    const imported = { ...ISSUED, importata_da: 'esterno' } as Invoice
    wrap(<InvoiceStateBadge invoice={imported} importata={false} />)
    expect(screen.getByText('Emessa')).toBeInTheDocument()
    expect(screen.queryByText(/^importata$/i)).toBeNull()
  })

  // Whatever the column holds -- the value is provenance the CRM keeps for itself, not
  // copy -- the badge says only that the invoice came from elsewhere. A source name
  // reaching the screen is the defect this assertion exists to catch.
  it('never prints the provenance value, whatever it is', () => {
    const imported = { ...ISSUED, importata_da: 'qualcosaltro' } as Invoice
    wrap(<InvoiceStateBadge invoice={imported} />)
    expect(screen.getByText(/^importata$/i)).toBeInTheDocument()
    expect(screen.queryByText(/qualcosaltro/i)).toBeNull()
  })

  // REB-368: the two import kinds get two distinct, honest badges -- "esterno" names
  // no source (assertion above), "fatturapa" names the transmission standard itself,
  // because unlike a hand-typed "esterno" row this one's original file is on record
  // and `export_xml` can hand it back.
  it('shows a distinct "FatturaPA" badge for a fatturapa-imported invoice', () => {
    const imported = { ...ISSUED, importata_da: 'fatturapa' } as Invoice
    wrap(<InvoiceStateBadge invoice={imported} />)
    expect(screen.getByText('FatturaPA')).toBeInTheDocument()
    expect(screen.queryByText(/^importata$/i)).toBeNull()
  })

  /** Unlike the "esterno" pill (test above), the "fatturapa" badge ignores the list's
   *  own suppression flag: a bulk "esterno" migration explains nothing most rows can
   *  act on (ORB-130), but a "fatturapa" row names a fact the list can act on -- the
   *  original file is on record and downloadable. */
  it('keeps the "FatturaPA" badge even where the list suppresses the generic one', () => {
    const imported = { ...ISSUED, importata_da: 'fatturapa' } as Invoice
    wrap(<InvoiceStateBadge invoice={imported} importata={false} />)
    expect(screen.getByText('Emessa')).toBeInTheDocument()
    expect(screen.getByText('FatturaPA')).toBeInTheDocument()
  })
})
