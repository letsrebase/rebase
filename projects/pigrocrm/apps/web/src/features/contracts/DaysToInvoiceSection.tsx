import { useNavigate } from '@tanstack/react-router'
import { FilePlus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Skeleton } from '@rebase/ui/skeleton'
import { formatDate } from '@/features/invoices/format'
import { useCan } from '@/lib/auth'
import { formatIsoMonthYear } from '@/lib/dates'
import { monthBounds, useBillableDays, useInvoiceDays } from './billingQueries'
import { formatAmount } from './labels'

const ALL_MONTHS = 'tutti'

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/**
 * The «Giornate da fatturare» section of a contract's page (REB-751): the worked days that
 * are ready to invoice, as the lines the draft would carry at the contract's rates, and the
 * button that makes the draft. The figures are the server's (`GET .../billable-days` builds
 * the lines the way the invoice does), never summed here, so what the page promises is
 * what the invoice then says.
 *
 * What is ready is a day `lavorato` and on no invoice yet. A day worked without the
 * approval the contract requires is not, and the section says how many are waiting for one,
 * which is the reason a month can look short. A day no rate card covers stops the button
 * and is named, because the price of a day is not something this page may invent.
 */
export function DaysToInvoiceSection({ contractId, divisa }: { contractId: string; divisa: string }) {
  const canBill = useCan('bill_contract_days')
  const navigate = useNavigate()
  const [month, setMonth] = useState(ALL_MONTHS)
  const overview = useBillableDays(contractId)
  const months = overview.data?.mesi ?? []
  // A month chosen and then billed leaves the list: back to all of them rather than a
  // select showing a value it no longer offers.
  const selected = month === ALL_MONTHS || months.includes(month) ? month : ALL_MONTHS
  const period = selected === ALL_MONTHS ? {} : monthBounds(selected)
  const chosen = useBillableDays(contractId, period)
  const invoice = useInvoiceDays(contractId)

  const ready = chosen.data

  function create() {
    invoice.mutate(period, {
      onSuccess: (draft) => {
        toast.success('Bozza di fattura creata con le giornate scelte')
        void navigate({ to: '/app/invoices/$invoiceId', params: { invoiceId: draft.id } })
      },
    })
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="mb-3 font-semibold">Giornate da fatturare</h2>

        {overview.isLoading || chosen.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : overview.isError ? (
          <QueryErrorBanner error={overview.error} />
        ) : chosen.isError ? (
          <QueryErrorBanner error={chosen.error} />
        ) : ready === undefined ? null : (
          <div className="space-y-4">
            {ready.righe.length === 0 ? (
              <p className="text-muted-foreground">Nessuna giornata pronta da fatturare.</p>
            ) : (
              <>
                {months.length > 1 && (
                  <div className="max-w-xs space-y-2">
                    <Label htmlFor="billable-period">Periodo</Label>
                    <Select value={selected} onValueChange={setMonth}>
                      <SelectTrigger id="billable-period" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={ALL_MONTHS}>Tutte le giornate pronte</SelectItem>
                        {months.map((value) => (
                          <SelectItem key={value} value={value}>
                            {formatIsoMonthYear(`${value}-01`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}

                <ul className="divide-y">
                  {ready.righe.map((riga) => (
                    <li
                      key={`${riga.mese}-${riga.descrizione}-${riga.prezzo_unitario}`}
                      className="flex items-center justify-between gap-4 py-3"
                    >
                      <div className="min-w-0">
                        <p className="font-medium">{riga.descrizione}</p>
                        <p className="text-sm text-muted-foreground">
                          {plural(riga.giornate, 'giornata', 'giornate')} a{' '}
                          {formatAmount(riga.prezzo_unitario, divisa)} per {riga.unita_misura}
                        </p>
                      </div>
                      <span className="shrink-0 tabular-nums">
                        {formatAmount(riga.importo, divisa)}
                      </span>
                    </li>
                  ))}
                  <li className="flex items-center justify-between gap-4 py-3 font-semibold">
                    <span>Imponibile</span>
                    <span className="tabular-nums">{formatAmount(ready.imponibile, divisa)}</span>
                  </li>
                </ul>
              </>
            )}

            {ready.senza_tariffa.length > 0 && (
              <p role="alert" className="text-sm text-destructive">
                {plural(ready.senza_tariffa.length, 'giornata non ha', 'giornate non hanno')} una
                tariffa in vigore alla propria data ({ready.senza_tariffa.map(formatDate).join(', ')}
                ): aggiungi o estendi una tariffa e la bozza si potrà creare.
              </p>
            )}

            {ready.senza_approvazione > 0 && (
              <p className="text-sm text-muted-foreground">
                {plural(ready.senza_approvazione, 'giornata lavorata', 'giornate lavorate')} senza
                l’approvazione che il contratto richiede: {ready.senza_approvazione === 1 ? 'si fattura' : 'si fatturano'}{' '}
                dopo che ne registri una.
              </p>
            )}

            {invoice.isError && <QueryErrorBanner error={invoice.error} />}

            {canBill && ready.righe.length > 0 && (
              <Button onClick={create} disabled={!ready.fatturabile || invoice.isPending}>
                <FilePlus className="mr-2 size-4" aria-hidden="true" />
                {invoice.isPending ? 'Creazione…' : 'Crea la bozza di fattura'}
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
