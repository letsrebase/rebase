import { useEffect, useRef, useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toProblem, type ProblemDetail } from '@/lib/api'
import {
  purchaseOrderChanges,
  purchaseOrderDraft,
  validatePurchaseOrder,
  type PurchaseOrderDraft,
} from './purchaseOrder'
import { useUpdateInvoice, type Invoice } from './queries'

/**
 * The customer's purchase order on a document that is still editable (REB-693): the
 * number, its date, the CIG and the CUP, which the XML writes as `DatiOrdineAcquisto`
 * and the PDF prints under the accrual period. Its own section and button, beside
 * `InvoiceHeaderEditor`, whose section is the document's dates.
 *
 * Saved through `PATCH /api/invoices/{id}` with only the fields the person edited,
 * `null` for an empty one; the rest follows the stored order, so a CIG added elsewhere
 * (MCP, another tab) is shown and never erased by saving another field. The freeze is the server's: an issued fattura or a consumed proforma answers 409,
 * and the banner shows it as it comes.
 */
export function PurchaseOrderEditor({ invoice }: { invoice: Invoice }) {
  const update = useUpdateInvoice(invoice.id)
  const [draft, setDraft] = useState<PurchaseOrderDraft>(() => purchaseOrderDraft(invoice))
  const [touched, setTouched] = useState<ReadonlySet<keyof PurchaseOrderDraft>>(new Set())
  const touchedRef = useRef(touched)
  useEffect(() => {
    touchedRef.current = touched
  }, [touched])
  const [error, setError] = useState<string | undefined>(undefined)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  // The stored order moved (a refetch after a change made elsewhere): every field the
  // person has not edited follows it, the ones they typed in keep their text.
  const { ordine_numero, ordine_data, ordine_cig, ordine_cup } = invoice
  useEffect(() => {
    const stored = purchaseOrderDraft({ ordine_numero, ordine_data, ordine_cig, ordine_cup })
    setDraft((current) => {
      const next = { ...current }
      for (const field of Object.keys(stored) as (keyof PurchaseOrderDraft)[]) {
        if (!touchedRef.current.has(field)) next[field] = stored[field]
      }
      return next
    })
  }, [ordine_numero, ordine_data, ordine_cig, ordine_cup])

  function set(field: keyof PurchaseOrderDraft, value: string) {
    setDraft((current) => ({ ...current, [field]: value }))
    setTouched((current) => new Set(current).add(field))
    setError(undefined)
    setProblem(null)
  }

  function submit() {
    setProblem(null)
    if (touched.size === 0) return
    // The order as it will stand: what the person edited over what is stored.
    const stored = purchaseOrderDraft(invoice)
    const merged = { ...stored }
    for (const field of touched) merged[field] = draft[field]
    const found = validatePurchaseOrder(merged)
    setError(found)
    if (found !== undefined) return
    update.mutate(purchaseOrderChanges(draft, touched), {
      onSuccess: (saved) => {
        setDraft(purchaseOrderDraft(saved))
        setTouched(new Set())
        toast.success('Ordine salvato')
      },
      onError: (failure) => setProblem(toProblem(failure)),
    })
  }

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">Riferimento ordine</h2>
      {problem ? <QueryErrorBanner error={problem} /> : null}

      <div className="grid max-w-2xl gap-4 sm:grid-cols-4">
        <div className="space-y-2">
          <Label htmlFor="ordine-numero">Numero ordine</Label>
          <Input
            id="ordine-numero"
            value={draft.ordine_numero}
            disabled={update.isPending}
            onChange={(event) => set('ordine_numero', event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ordine-data">Data ordine</Label>
          <Input
            id="ordine-data"
            type="date"
            value={draft.ordine_data}
            disabled={update.isPending}
            onChange={(event) => set('ordine_data', event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ordine-cig">CIG</Label>
          <Input
            id="ordine-cig"
            value={draft.ordine_cig}
            disabled={update.isPending}
            onChange={(event) => set('ordine_cig', event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ordine-cup">CUP</Label>
          <Input
            id="ordine-cup"
            value={draft.ordine_cup}
            disabled={update.isPending}
            onChange={(event) => set('ordine_cup', event.target.value)}
          />
        </div>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="flex items-center justify-between gap-4">
        <p className="text-muted-foreground text-xs">
          L’ordine del cliente finisce nell’XML della fattura (DatiOrdineAcquisto) e sul PDF. Data,
          CIG e CUP richiedono il numero; per togliere l’ordine svuota tutti i campi.
        </p>
        <Button variant="outline" size="sm" onClick={submit} disabled={update.isPending}>
          Salva ordine
        </Button>
      </div>
    </section>
  )
}
