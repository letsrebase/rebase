import { Link } from '@tanstack/react-router'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Skeleton } from '@rebase/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { formatIsoDateItalian, formatIsoMonthYear } from '@/lib/dates'
import { BarRows, BigNumber, type BarRow } from './charts'
import { money } from './format'
import { Freshness } from './Freshness'
import { useReceivablesDashboard, type FasciaScadenza } from './queries'

/** The overdue bucket is the one outcome among six steps: the destructive tint the
 *  pipeline card already uses for «Perso», and no sixth colour. */
const SCADUTO_FILL = 'color-mix(in oklab, var(--destructive) 10%, transparent)'

function fasciaBar(row: FasciaScadenza, index: number): BarRow {
  return {
    label: row.numero > 0 ? `${row.etichetta} (${row.numero})` : row.etichetta,
    value: money(row.importo),
    // The share is the server's (`quota`, in [0, 1]); nothing here divides an amount.
    ratio: row.quota,
    tone: index + 1,
    color: row.codice === 'scaduto' ? SCADUTO_FILL : undefined,
  }
}

function solleciti(inviati: number, ultimo: string | null): string {
  if (inviati === 0) return 'nessuno'
  const quanti = inviati === 1 ? '1 sollecito' : `${inviati} solleciti`
  return ultimo ? `${quanti}, ultimo il ${formatIsoDateItalian(ultimo)}` : quanti
}

/**
 * Slice 8 part A (REB-329): when the money already invoiced arrives.
 *
 * No period: what is owed is owed today, and `oggi` in the response is the day every
 * bucket is measured from. Four pictures of the same set, `_receivable_filter` on the
 * server: the six ageing buckets (whose sum is the economic tab's «da incassare», to the
 * cent), the cash expected by due month, who owes what, and the overdue rows with the
 * reminders that actually left. Every figure is the string the API sent; the bars scale
 * on the server's own `quota`.
 */
export function ReceivablesTab() {
  const query = useReceivablesDashboard()

  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  const data = query.data
  const scaduto = data.fasce.find((row) => row.codice === 'scaduto')
  const mesi: BarRow[] = data.per_mese.map((row, index) => ({
    label: `${formatIsoMonthYear(row.mese)} (${row.numero})`,
    value: money(row.importo),
    ratio: row.quota,
    tone: (index % 5) + 1,
  }))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Fatture emesse e non incassate al {formatIsoDateItalian(data.oggi)}, per scadenza.
        </p>
        <Freshness calcolatoAlle={data.calcolato_alle} onRefresh={() => void query.refetch()} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <BigNumber label="Da incassare" value={money(data.totale)} tone="accent" />
        <BigNumber
          label="Di cui scaduto"
          value={money(scaduto?.importo ?? '0.00')}
          hint={`${data.scadute_totale} ${data.scadute_totale === 1 ? 'fattura scaduta' : 'fatture scadute'}`}
        />
      </div>

      <BarRows caption="Scadenziario per fascia" rows={data.fasce.map(fasciaBar)} />
      {scaduto?.collegamento ? (
        <div className="flex justify-end">
          <Button variant="outline" size="sm" asChild>
            <Link to="/app/invoices" search={{ scadute: true }}>
              Vedi le fatture scadute
            </Link>
          </Button>
        </div>
      ) : null}

      <BarRows caption="Incassi attesi per mese di scadenza" rows={mesi} />

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Esposizione per cliente</h2>
        {data.per_cliente.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura da incassare.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cliente</TableHead>
                <TableHead className="text-right">Fatture</TableHead>
                <TableHead className="text-right">Da incassare</TableHead>
                <TableHead className="text-right">Di cui scaduto</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.per_cliente.map((row) => (
                <TableRow key={row.customer_id}>
                  <TableCell>
                    <Link
                      to="/app/customers/$customerId"
                      params={{ customerId: row.customer_id }}
                      className="underline-offset-4 hover:underline"
                    >
                      {row.ragione_sociale}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right">{row.numero}</TableCell>
                  <TableCell className="text-right">{money(row.importo)}</TableCell>
                  <TableCell className="text-right">{money(row.scaduto)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Fatture scadute</h2>
        {data.scadute.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura scaduta.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Numero</TableHead>
                <TableHead>Cliente</TableHead>
                <TableHead>Scadenza</TableHead>
                <TableHead className="text-right">Ritardo</TableHead>
                <TableHead className="text-right">Importo</TableHead>
                <TableHead>Solleciti</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.scadute.map((row) => (
                <TableRow key={row.invoice_id}>
                  <TableCell>
                    <Link
                      to="/app/invoices/$invoiceId"
                      params={{ invoiceId: row.invoice_id }}
                      className="underline-offset-4 hover:underline"
                    >
                      {row.numero}
                    </Link>
                  </TableCell>
                  <TableCell>{row.cliente}</TableCell>
                  <TableCell>{formatIsoDateItalian(row.data_scadenza)}</TableCell>
                  <TableCell className="text-right">{row.giorni_di_ritardo} gg</TableCell>
                  <TableCell className="text-right">{money(row.importo)}</TableCell>
                  <TableCell>{solleciti(row.solleciti_inviati, row.ultimo_sollecito_il)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {data.scadute_totale > data.scadute.length ? (
          <p className="text-xs text-muted-foreground">
            Mostrate {data.scadute.length} fatture scadute su {data.scadute_totale}.
          </p>
        ) : null}
      </section>
    </div>
  )
}
