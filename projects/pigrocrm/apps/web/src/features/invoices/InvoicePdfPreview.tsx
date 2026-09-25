import { FileText } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { Skeleton } from '@rebase/ui/skeleton'
import { useInvoicePdf, type Invoice } from './queries'

/**
 * The document itself, beside its numbers (ORB-30).
 *
 * The bytes come through `useInvoicePdf`, the same authenticated path the «PDF» button
 * downloads from: the API is the only place authorisation exists on either storage
 * backend (slice 2 §5), so an `<iframe src="/api/...">` would be wrong twice -- it could
 * not carry the tenant prefix and the refresh, and the route answers
 * `Content-Disposition: attachment`, which a frame would turn into a download.
 *
 * The object URL is made here from the cached Blob and revoked when the frame leaves:
 * cached in the query instead, it would come back revoked on the next mount. It is made
 * in `PdfFrame`'s effect, after the render commits, never during a render: a render
 * React throws away runs no cleanup, so a URL made there would never be revoked.
 *
 * Only a PDF reaches the frame (REB-463). The Blob's type is the response's
 * `Content-Type`, which is the current version's own. Until REB-480 anyone who may write
 * could add a version of any allowed type to the invoice's document, `application/xml`
 * among them, and a `blob:` URL has this page's origin, so an XML file in the XHTML
 * namespace rendered here as a page of the app: its markup always, a form included, and
 * its script wherever no CSP forbids inline script. The server now refuses such a
 * version and answers 404 for one written before, so this check is the second line: a
 * Blob that is not `application/pdf` still gets no object URL, and the pane says so.
 */
export function InvoicePdfPreview({ invoice }: { invoice: Invoice }) {
  const pdf = useInvoicePdf(invoice)
  const blob = pdf.data

  const empty = invoice.pdf_document_id === null
  return (
    <aside
      aria-label="Anteprima PDF"
      aria-busy={!empty && pdf.isPending}
      // Sticky in its grid column from `xl`, as tall as the viewport minus the page
      // header (PageHeader, its tabs: 10.25rem today). The panel's own 12px top margin
      // went with REB-328, and the scrollport is the whole window now, so the offset
      // drops by that margin and the pane still ends where it did. `self-start` is what
      // keeps a sticky item from being stretched to the row.
      className="bg-muted/40 flex min-h-[32rem] flex-col overflow-hidden border xl:sticky xl:top-6 xl:h-[calc(100vh-10.25rem)] xl:self-start"
    >
      {empty ? (
        <Empty invoice={invoice} />
      ) : pdf.isError ? (
        toProblem(pdf.error).status === 404 ? (
          <Missing invoice={invoice} />
        ) : (
          <div className="p-4">
            <QueryErrorBanner error={pdf.error} />
          </div>
        )
      ) : !blob ? (
        <>
          <p className="sr-only">Caricamento del PDF…</p>
          <Skeleton className="m-4 flex-1" />
        </>
      ) : !isPdf(blob) ? (
        <NotPdf invoice={invoice} />
      ) : (
        <PdfFrame blob={blob} title={`Anteprima PDF ${invoice.tipo === 'proforma' ? 'proforma' : 'fattura'}`} />
      )}
    </aside>
  )
}

/** Only ever given an `application/pdf` Blob: the caller checks `isPdf` first. */
function PdfFrame({ blob, title }: { blob: Blob; title: string }) {
  const frame = useRef<HTMLIFrameElement>(null)
  useEffect(() => {
    const node = frame.current
    if (!node) return
    const url = URL.createObjectURL(blob)
    // A hint for the viewer's chrome: Chromium honours it, Firefox and Safari ignore
    // it. The bar's own «PDF» button is the download either way.
    // Snyk Code flags this as javascript/DOMXSS. It was real until REB-463: an XHTML
    // version of the invoice's document rendered here as the app. Now only an
    // application/pdf Blob reaches this component, and since REB-480 the server serves
    // nothing else on this route.
    node.src = `${url}#toolbar=0&navpanes=0`
    return () => URL.revokeObjectURL(url)
  }, [blob])
  return <iframe ref={frame} title={title} className="h-full w-full flex-1 border-0" />
}

/**
 * No PDF is not an error, and each case names the button that changes it -- only when
 * such a button exists. A draft gets its PDF at emission; a proforma from «Genera PDF
 * proforma»; an imported invoice has no rendering of ours and never will (the system
 * that issued it holds the original); anything else from «Rigenera documenti». Both
 * buttons are `produce_invoice_artifacts` on the bar, so a role without it is told the
 * button exists and is not theirs, as `InvoiceActions` says it.
 */
function Empty({ invoice }: { invoice: Invoice }) {
  const mayProduce = useCan('produce_invoice_artifacts')
  const text =
    invoice.tipo === 'proforma'
      ? mayProduce
        ? 'Il PDF della proforma non è ancora stato generato: «Genera PDF proforma» lo produce.'
        : 'Il PDF della proforma non è ancora stato generato: si produce con «Genera PDF proforma», che il tuo ruolo non può usare.'
      : invoice.stato === 'bozza'
        ? 'Il PDF si genera all’emissione. Fino ad allora la bozza è solo numeri.'
        : invoice.importata_da != null
          ? 'Fattura importata: il PDF originale non è archiviato qui.'
          : mayProduce
            ? 'Nessun PDF archiviato per questo documento. «Rigenera documenti» lo produce.'
            : 'Nessun PDF archiviato per questo documento. Si produce con «Rigenera documenti», che il tuo ruolo non può usare.'
  return <Notice text={text} />
}

/**
 * The row names a PDF and the server answers 404 for it: the document has no current
 * version, or its stored file is gone. The server's detail for either is a log line
 * (`invoice_artifact <uuid>#pdf not found`, `document_blob <key> not found`), so this says
 * it in Italian instead (REB-168), in the same voice as `Empty`, and names «Rigenera
 * documenti» only where it is on the page: an issued fattura of ours, since that button
 * repairs a lost file with identical bytes, and only to a role that has it (REB-294
 * hides it from a readonly one, and `InvoiceActions` words it the same way). A
 * proforma's «Genera PDF proforma» is not shown while the row carries an id, and an
 * imported invoice has no rendering of ours.
 */
function Missing({ invoice }: { invoice: Invoice }) {
  const regenerate = useRegenerateHint(invoice)
  const text =
    invoice.tipo === 'proforma'
      ? 'Il PDF di questa proforma non è disponibile.'
      : regenerate === 'press'
        ? 'Il PDF di questa fattura non è disponibile. «Rigenera documenti» lo genera di nuovo.'
        : regenerate === 'role'
          ? 'Il PDF di questa fattura non è disponibile. Si genera di nuovo con «Rigenera documenti», che il tuo ruolo non può usare.'
          : 'Il PDF di questa fattura non è disponibile.'
  return <Notice text={text} />
}

/**
 * The response carried bytes that are not a PDF (REB-463): a version of another type on
 * the invoice's document. Since REB-480 the server refuses one and answers 404 for one
 * written before, which `Missing` words, so this is the client's own second line. It
 * names «Rigenera documenti» where `Missing` does, since on an issued fattura of ours
 * that button stores a fresh PDF as the document's next version; elsewhere nothing on
 * the page makes one.
 */
function NotPdf({ invoice }: { invoice: Invoice }) {
  const regenerate = useRegenerateHint(invoice)
  const which = invoice.tipo === 'proforma' ? 'questa proforma' : 'questa fattura'
  const why = `Il file archiviato per ${which} non è un PDF, quindi l’anteprima non lo mostra.`
  const how =
    regenerate === 'press'
      ? ' «Rigenera documenti» genera di nuovo il PDF.'
      : regenerate === 'role'
        ? ' Si genera di nuovo con «Rigenera documenti», che il tuo ruolo non può usare.'
        : ''
  return <Notice text={why + how} />
}

/**
 * Whether «Rigenera documenti» is the way out of a missing or wrong file: `press` when
 * it is on this person's bar, `role` when it would be but their role hides it, `null`
 * when it is on nobody's bar (a proforma, a draft, an imported fattura).
 */
function useRegenerateHint(invoice: Invoice): 'press' | 'role' | null {
  const mayProduce = useCan('produce_invoice_artifacts')
  const ours = invoice.tipo !== 'proforma' && invoice.stato === 'emessa' && invoice.importata_da == null
  if (!ours) return null
  return mayProduce ? 'press' : 'role'
}

function Notice({ text }: { text: string }) {
  return (
    <div className="text-muted-foreground m-auto flex max-w-xs flex-col items-center gap-3 p-6 text-center text-sm">
      <FileText className="size-8" aria-hidden="true" />
      <p>{text}</p>
    </div>
  )
}

/** `application/pdf` by its essence: a parameter after `;` does not make it another type. */
function isPdf(blob: Blob): boolean {
  return (blob.type.split(';', 1)[0] ?? '').trim().toLowerCase() === 'application/pdf'
}
