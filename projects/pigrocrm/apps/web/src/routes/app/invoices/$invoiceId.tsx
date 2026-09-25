import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Receipt } from 'lucide-react'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Loader } from '@rebase/ui/loader'
import { ConsumedProformaNotice } from '@/features/invoices/ConsumedProformaNotice'
import { InvoiceActions } from '@/features/invoices/InvoiceActions'
import { InvoiceHeaderEditor } from '@/features/invoices/InvoiceHeaderEditor'
import { InvoiceLinesEditor } from '@/features/invoices/InvoiceLinesEditor'
import { InvoicePdfPreview } from '@/features/invoices/InvoicePdfPreview'
import { InvoiceStateBadge } from '@/features/invoices/InvoiceStateBadge'
import {
  formatDate,
  formatInvoiceNumber,
  formatMoney,
  formatPeriod,
} from '@/features/invoices/format'
import {
  PAYMENT_STATE_LABELS,
  PAYMENT_STATE_TONE,
  useInvoice,
  useInvoiceLines,
  type StatoPagamento,
} from '@/features/invoices/queries'
import { useCan } from '@/lib/auth'

export function InvoiceDetail() {
  const { invoiceId } = Route.useParams()
  const navigate = useNavigate()
  // REB-294: the editors below save through `update_invoice`/`replace_invoice_lines`,
  // both `collaboratore` in the service; a readonly person gets the stated rows rather
  // than inputs that could only answer 403. Called before the early returns below --
  // hooks cannot live after one.
  const canEditInvoice = useCan('update_invoice')
  const invoice = useInvoice(invoiceId)
  const lines = useInvoiceLines(invoiceId)
  if (invoice.isLoading) {
    return (
      <p className="flex items-center gap-2 p-8">
        <Loader className="size-4" />
        Caricamento…
      </p>
    )
  }
  if (invoice.isError) {
    return (
      <div className="p-8">
        <QueryErrorBanner error={invoice.error} />
      </div>
    )
  }
  if (!invoice.data) return <p className="p-8">Fattura non trovata.</p>

  const row = invoice.data
  // The row's own state decides, never a prop a caller chose: an issued invoice's lines
  // are immutable in the database, so offering inputs would invite an edit that cannot
  // be saved. A proforma stays editable until it is consumed. The role is the other
  // half (REB-294): a readonly person sees the same stated rows an issued invoice
  // already renders.
  const readOnly =
    !canEditInvoice ||
    !(row.stato === 'bozza' || (row.tipo === 'proforma' && row.stato !== 'consumata'))
  // Only a fattura has money to collect: a proforma reads as a dash in the list for the
  // same reason (`columns.tsx`), and a second pill here would claim a state it has not.
  const pagamento = row.tipo === 'fattura' ? (row.stato_pagamento as StatoPagamento) : null

  return (
    <EntityDetailLayout
      icon={Receipt}
      title={formatInvoiceNumber(row)}
      subtitle={row.causale ?? undefined}
      entityType="invoice"
      entityId={row.id}
      actions={
        <>
          <InvoiceStateBadge invoice={row} />
          {pagamento !== null ? (
            <StatusPill tone={PAYMENT_STATE_TONE[pagamento]}>
              {PAYMENT_STATE_LABELS[pagamento]}
            </StatusPill>
          ) : null}
        </>
      }
      overview={
        // Half and half from a wide screen (ORB-30): the numbers on the left as they
        // were, the document itself on the right. Below that width the preview follows
        // the data, since the numbers are what a phone came for.
        <div className="grid gap-8 xl:grid-cols-2">
          <div className="space-y-8">
            {/* A deleted draft has no page: back to the list, which the mutation has already
                invalidated. */}
            <InvoiceActions
              invoice={row}
              onDeleted={() => void navigate({ to: '/app/invoices' })}
              // From a proforma the numbered row is a new one and this page has just
              // become a consumed proforma with nothing to do on it, so go where the
              // XML is. A draft fattura is issued in place and stays (ORB-134).
              onIssued={(issued) => {
                if (issued.id !== row.id)
                  void navigate({ to: '/app/invoices/$invoiceId', params: { invoiceId: issued.id } })
              }}
            />
            {row.tipo === 'proforma' && row.stato === 'consumata' ? (
              <ConsumedProformaNotice proforma={row} />
            ) : null}

            {/* While the document can still change, its dates are inputs (ORB-61, ORB-63)
                and the list below states only what nobody can edit here; once frozen, the
                same facts come back as rows. Stated or asked, never both. */}
            {readOnly ? null : <InvoiceHeaderEditor invoice={row} />}

            <dl className="grid max-w-lg grid-cols-2 gap-2 text-sm">
              {readOnly ? (
                <>
                  {/* «Data» on a proforma: it is the customer's document date and not
                      an emission, which is the fiscal act a fattura's date names. */}
                  <dt className="text-muted-foreground">
                    {row.tipo === 'proforma' ? 'Data' : 'Data emissione'}
                  </dt>
                  <dd>{formatDate(row.data_emissione)}</dd>
                </>
              ) : null}
              <dt className="text-muted-foreground">Scadenza</dt>
              <dd>{formatDate(row.data_scadenza)}</dd>
              {readOnly ? (
                <>
                  <dt className="text-muted-foreground">Periodo di competenza</dt>
                  <dd>{formatPeriod(row.competenza_da, row.competenza_a)}</dd>
                </>
              ) : null}
              <dt className="text-muted-foreground">Imponibile</dt>
              <dd>{formatMoney(row.imponibile)}</dd>
              <dt className="text-muted-foreground">Imposta</dt>
              <dd>{formatMoney(row.imposta)}</dd>
              {/* Stored but outside the total: DatiBollo declares that the issuer settled
                  it virtually, so adding it here would overstate what the customer owes. */}
              <dt className="text-muted-foreground">Bollo</dt>
              <dd>{formatMoney(row.bollo)}</dd>
              <dt className="text-muted-foreground font-medium">Totale</dt>
              <dd className="font-medium">{formatMoney(row.totale)}</dd>
              {pagamento !== null ? (
                <>
                  <dt className="text-muted-foreground">Pagamento</dt>
                  <dd>
                    {PAYMENT_STATE_LABELS[pagamento]}
                    {row.data_incasso !== null ? ` il ${formatDate(row.data_incasso)}` : ''}
                  </dd>
                </>
              ) : null}
              {row.trasmessa_esternamente_il !== null ? (
                <>
                  <dt className="text-muted-foreground">Trasmessa il</dt>
                  <dd>{formatDate(row.trasmessa_esternamente_il)}</dd>
                </>
              ) : null}
              {row.annullata_il !== null ? (
                <>
                  <dt className="text-muted-foreground">Annullata il</dt>
                  <dd>{formatDate(row.annullata_il)}</dd>
                  <dt className="text-muted-foreground">Motivo</dt>
                  <dd>{row.motivo_annullamento ?? '—'}</dd>
                </>
              ) : null}
            </dl>

            <section className="space-y-3">
              <h2 className="text-sm font-medium">Righe</h2>
              {lines.isError ? (
                <QueryErrorBanner error={lines.error} />
              ) : (
                <InvoiceLinesEditor invoice={row} lines={lines.data ?? []} readOnly={readOnly} />
              )}
            </section>
          </div>
          <InvoicePdfPreview invoice={row} />
        </div>
      }
    />
  )
}

export const Route = createFileRoute('/app/invoices/$invoiceId')({ component: InvoiceDetail })
