import { FileText } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toProblem } from '@/lib/api'
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
 * The object URL is made here from the cached Blob and revoked when this component
 * leaves: cached in the query instead, it would come back revoked on the next mount.
 *
 * Only a PDF reaches the frame (REB-463). The Blob's type is the response's
 * `Content-Type`, which is the current version's own, and anyone who may write can add
 * a version of any allowed type to the invoice's document, `application/xml` among
 * them. A `blob:` URL has this page's origin, so an XML file in the XHTML namespace
 * rendered here as a page of the app: its markup always, a form included, and its
 * script wherever no CSP forbids inline script. Anything that is not `application/pdf`
 * gets no object URL at all, and the pane says so instead.
 */
export function InvoicePdfPreview({ invoice }: { invoice: Invoice }) {
  const pdf = useInvoicePdf(invoice)
  const blob = pdf.data
  const notPdf = blob !== undefined && !isPdf(blob)
  // Derived from the bytes, revoked when they change or the component leaves.
  const url = useMemo(() => (blob && isPdf(blob) ? URL.createObjectURL(blob) : undefined), [blob])
  useEffect(() => {
    if (!url) return
    return () => URL.revokeObjectURL(url)
  }, [url])

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
      ) : notPdf ? (
        <NotPdf invoice={invoice} />
      ) : !url ? (
        <>
          <p className="sr-only">Caricamento del PDF…</p>
          <Skeleton className="m-4 flex-1" />
        </>
      ) : (
        <iframe
          title={`Anteprima PDF ${invoice.tipo === 'proforma' ? 'proforma' : 'fattura'}`}
          // A hint for the viewer's chrome: Chromium honours it, Firefox and Safari
          // ignore it. The bar's own «PDF» button is the download either way.
          // Snyk Code flags this as javascript/DOMXSS. It was real until REB-463: an
          // XHTML version of the invoice's document rendered here as the app. Now `url`
          // only exists for an application/pdf Blob (`isPdf` below).
          src={`${url}#toolbar=0&navpanes=0`}
          className="h-full w-full flex-1 border-0"
        />
      )}
    </aside>
  )
}

/**
 * No PDF is not an error, and each case names the button that changes it -- only when
 * such a button exists. A draft gets its PDF at emission; a proforma from «Genera PDF
 * proforma»; an imported invoice has no rendering of ours and never will (the system
 * that issued it holds the original); anything else from «Rigenera documenti».
 */
function Empty({ invoice }: { invoice: Invoice }) {
  const text =
    invoice.tipo === 'proforma'
      ? 'Il PDF della proforma non è ancora stato generato: «Genera PDF proforma» lo produce.'
      : invoice.stato === 'bozza'
        ? 'Il PDF si genera all’emissione. Fino ad allora la bozza è solo numeri.'
        : invoice.importata_da != null
          ? 'Fattura importata: il PDF originale non è archiviato qui.'
          : 'Nessun PDF archiviato per questo documento. «Rigenera documenti» lo produce.'
  return <Notice text={text} />
}

/**
 * The row names a PDF and the server answers 404 for it: the document has no current
 * version, or its stored file is gone. The server's detail for either is a log line
 * (`invoice_artifact <uuid>#pdf not found`, `document_blob <key> not found`), so this says
 * it in Italian instead (REB-168), in the same voice as `Empty`, and names «Rigenera
 * documenti» only where it is on the page: an issued fattura of ours, since that button
 * repairs a lost file with identical bytes. A proforma's «Genera PDF proforma» is not
 * shown while the row carries an id, and an imported invoice has no rendering of ours.
 */
function Missing({ invoice }: { invoice: Invoice }) {
  const text =
    invoice.tipo === 'proforma'
      ? 'Il PDF di questa proforma non è disponibile.'
      : invoice.stato === 'emessa' && invoice.importata_da == null
        ? 'Il PDF di questa fattura non è disponibile. «Rigenera documenti» lo genera di nuovo.'
        : 'Il PDF di questa fattura non è disponibile.'
  return <Notice text={text} />
}

/**
 * The server answered with bytes that are not a PDF (REB-463): somebody added a version
 * of another type to the invoice's document. It names «Rigenera documenti» where
 * `Missing` does, since on an issued fattura of ours that button stores a fresh PDF as
 * the document's next version; elsewhere nothing on the page makes one.
 */
function NotPdf({ invoice }: { invoice: Invoice }) {
  const which = invoice.tipo === 'proforma' ? 'questa proforma' : 'questa fattura'
  const why = `Il file archiviato per ${which} non è un PDF, quindi l’anteprima non lo mostra.`
  const ours = invoice.tipo !== 'proforma' && invoice.stato === 'emessa' && invoice.importata_da == null
  return <Notice text={ours ? `${why} «Rigenera documenti» genera di nuovo il PDF.` : why} />
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
