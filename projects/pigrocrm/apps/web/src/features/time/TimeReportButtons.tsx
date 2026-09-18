import { Download, FileSpreadsheet, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toIsoMonth } from '@/lib/dates'
import { timeReportXlsxUrl, useRenderTimeReportPdf } from './queries'

/**
 * Two formats, two recipients (§2.1): the PDF is attached to the invoice, the XLSX gets
 * filtered by whoever has to check it.
 *
 * They are not two variants of one control, and the first version of this component
 * treated them as though they were -- two anchors at the same endpoint with a different
 * `formato`. The XLSX is a working copy and the endpoint streams its bytes with a
 * `Content-Disposition`, so an anchor is exactly right there. The PDF is an *artefact*:
 * `render_pdf` archives it as a `document`, and the endpoint answers `201` with that
 * document's JSON, because slice 2's rule is that archived bytes are fetched from the
 * document download endpoint and nowhere else. Clicking «PDF» therefore navigated the
 * browser to a page of JSON. It is a mutation followed by a download, and it looks like
 * one now: a button that reports progress and can fail in place.
 */
export function TimeReportButtons({ dealId }: { dealId: string }) {
  // `toIsoMonth`, never `toISOString().slice(0, 7)` -- see @/lib/dates for why.
  const [mese, setMese] = useState(() => toIsoMonth(new Date()))
  const render = useRenderTimeReportPdf()

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-2">
          <Label htmlFor="time-report-mese">Rapporto ore del mese</Label>
          <Input
            id="time-report-mese"
            type="month"
            value={mese}
            onChange={(event) => setMese(event.target.value)}
            className="w-40"
          />
        </div>
        <Button
          variant="outline"
          disabled={render.isPending}
          onClick={() =>
            render.mutate(
              { dealId, mese },
              { onSuccess: () => toast.success('Rapporto ore archiviato e scaricato') },
            )
          }
        >
          {render.isPending ? (
            <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
          ) : (
            <Download className="mr-2 size-4" aria-hidden="true" />
          )}
          PDF
        </Button>
        <Button asChild variant="outline">
          <a href={timeReportXlsxUrl(dealId, mese)}>
            <FileSpreadsheet className="mr-2 size-4" aria-hidden="true" />
            XLSX
          </a>
        </Button>
      </div>
      {/* A refused render -- a month with no hours, a deal the actor cannot read -- has
          to say so here. The anchor beside it can only ever fail as a browser error
          page, which is the other half of why these two are not the same control. */}
      {render.isError ? <QueryErrorBanner error={render.error} /> : null}
    </div>
  )
}
