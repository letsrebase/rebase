import { FileText } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
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
 */
export function InvoicePdfPreview({ invoice }: { invoice: Invoice }) {
  const pdf = useInvoicePdf(invoice)
  const blob = pdf.data
  // Derived from the bytes, revoked when they change or the component leaves.
  const url = useMemo(() => (blob ? URL.createObjectURL(blob) : undefined), [blob])
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
      // header (PageHeader, its tabs and the panel's own top margin: 11rem today).
      // `self-start` is what keeps a sticky item from being stretched to the row.
      className="bg-muted/40 flex min-h-[32rem] flex-col overflow-hidden border xl:sticky xl:top-6 xl:h-[calc(100vh-11rem)] xl:self-start"
    >
      {empty ? (
        <Empty invoice={invoice} />
      ) : pdf.isError ? (
        <div className="p-4">
          <QueryErrorBanner error={pdf.error} />
        </div>
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
  return (
    <div className="text-muted-foreground m-auto flex max-w-xs flex-col items-center gap-3 p-6 text-center text-sm">
      <FileText className="size-8" aria-hidden="true" />
      <p>{text}</p>
    </div>
  )
}
