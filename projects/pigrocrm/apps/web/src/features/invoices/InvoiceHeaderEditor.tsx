import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { formatIsoDateItalian } from '@/lib/dates'
import { AccrualPeriodFields } from './AccrualPeriodFields'
import { accrualPeriodBody, validateAccrualPeriod, type AccrualPeriodDraft } from './accrualPeriod'
import { useUpdateInvoice, type Invoice } from './queries'

interface Errors {
  data?: string
  competenza?: string
}

/**
 * The dates of a document that is still editable: a proforma's own date (ORB-63) and
 * the accrual period (ORB-61). The detail page renders this in place of the read-only
 * rows while the document can change, so a date is either stated or asked, never both.
 *
 * A draft fattura has no date to edit here: `data_emissione` is assigned at emission
 * (`InvoiceService.issue`) and `InvoiceUpdate` refuses it on a fattura, so the input
 * exists only on a proforma. The period is asked on both.
 *
 * Saved through `PATCH /api/invoices/{id}`, the call `causale` and `note_interne`
 * already travel on. The freeze is the server's, not a prop: a consumed proforma or an
 * issued fattura answers 409, and the banner shows it as it comes.
 */
export function InvoiceHeaderEditor({ invoice }: { invoice: Invoice }) {
  const update = useUpdateInvoice(invoice.id)
  // `?? ''`: a `null` from the API, and also an absent key from an API that predates
  // the two columns, both read as "not set" rather than as the string "undefined".
  const [dataEmissione, setDataEmissione] = useState(invoice.data_emissione ?? '')
  // The due date (REB-326), on both kinds: written here it is what `issue` prints;
  // empty, the customer's terms decide at emission and `scadenza_prevista` (the
  // server's own forecast, never recomputed here) says what they would give today.
  const [dataScadenza, setDataScadenza] = useState(invoice.data_scadenza ?? '')
  const [competenza, setCompetenza] = useState<AccrualPeriodDraft>({
    competenza_da: invoice.competenza_da ?? '',
    competenza_a: invoice.competenza_a ?? '',
  })
  const [errors, setErrors] = useState<Errors>({})
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const isProforma = invoice.tipo === 'proforma'

  function submit() {
    const found: Errors = {}
    if (isProforma && dataEmissione === '') found.data = 'La proforma ha bisogno di una data.'
    const periodError = validateAccrualPeriod(competenza)
    if (periodError !== undefined) found.competenza = periodError
    setErrors(found)
    setProblem(null)
    if (Object.keys(found).length > 0) return

    // The period always, `null` when cleared (see `accrualPeriodBody`); the date only
    // where the server accepts it.
    const body: Record<string, unknown> = accrualPeriodBody(competenza)
    if (isProforma) body.data_emissione = dataEmissione
    // Sent only when it changed: an explicit `null` is how the server clears it back to
    // the terms, and an untouched empty input must not clear anything.
    if (dataScadenza !== (invoice.data_scadenza ?? '')) {
      body.data_scadenza = dataScadenza === '' ? null : dataScadenza
    }
    update.mutate(body, {
      onSuccess: () => toast.success('Date salvate'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">Date del documento</h2>
      {problem ? <QueryErrorBanner error={problem} /> : null}

      <div className="grid max-w-2xl gap-4 sm:grid-cols-3">
        {isProforma ? (
          <div className="space-y-2">
            <Label htmlFor="fattura-data">Data</Label>
            <Input
              id="fattura-data"
              type="date"
              required
              value={dataEmissione}
              onChange={(event) => setDataEmissione(event.target.value)}
            />
          </div>
        ) : null}
        <AccrualPeriodFields idPrefix="fattura" value={competenza} onChange={setCompetenza} />
        <div className="space-y-2">
          <Label htmlFor="fattura-scadenza">Scadenza</Label>
          <Input
            id="fattura-scadenza"
            type="date"
            value={dataScadenza}
            onChange={(event) => setDataScadenza(event.target.value)}
          />
        </div>
      </div>
      {dataScadenza === '' && invoice.scadenza_prevista ? (
        <p className="text-muted-foreground text-xs">
          Scadenza vuota: all’emissione viene calcolata dai termini del cliente:{' '}
          {formatIsoDateItalian(invoice.scadenza_prevista)} se emessa oggi.
        </p>
      ) : null}
      {errors.data ? <p className="text-sm text-destructive">{errors.data}</p> : null}
      {errors.competenza ? <p className="text-sm text-destructive">{errors.competenza}</p> : null}

      <div className="flex items-center justify-between gap-4">
        {/* Where each date goes, in one line: the period is what the P&L «per competenza»
            and the XML's DataInizioPeriodo/DataFinePeriodo read; a proforma's date is
            the customer's, and the fattura issued from it takes its own. */}
        <p className="text-muted-foreground text-xs">
          {isProforma
            ? 'La data è quella stampata sulla proforma; la fattura che ne nasce prende la propria all’emissione.'
            : 'La data di emissione viene assegnata all’emissione.'}{' '}
          Il periodo di competenza dice a quale mese appartiene il lavoro. La scadenza scritta
          qui vince sui termini del cliente.
        </p>
        <Button variant="outline" size="sm" onClick={submit} disabled={update.isPending}>
          Salva date
        </Button>
      </div>
    </section>
  )
}
