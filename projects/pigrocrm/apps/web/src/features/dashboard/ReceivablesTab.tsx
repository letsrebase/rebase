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
import { useReceivablesDashboard, type BandDueDate } from './queries'

/** The overdue bucket is the one outcome among six steps: the destructive tint the
 *  pipeline card already uses for «Perso», and no sixth colour. */
const ExpiredFill = 'color-mix(in oklab, var(--destructive) 10%, transparent)'

function bandBar(row: BandDueDate, index: number): BarRow {
  return {
    label: row.number > 0 ? `${row.label} (${row.number})` : row.label,
    value: money(row.amount),
    // The share is the server's (`share`, in [0, 1]); nothing here divides an amount.
    ratio: row.share,
    tone: index + 1,
    color: row.code === 'expired' ? ExpiredFill : undefined,
  }
}

function payment_reminders(sent: number, last: string | null): string {
  if (sent === 0) return 'nessuno'
  const how_many = sent === 1 ? '1 sollecito' : `${sent} solleciti`
  return last ? `${how_many}, ultimo il ${formatIsoDateItalian(last)}` : how_many
}

/**
 * Slice 8 part A (REB-329): when the money already invoiced arrives.
 *
 * No period: what is owed is owed today, and `today` in the response is the day every
 * bucket is measured from. Four pictures of the same set, `_receivable_filter` on the
 * server: the six ageing buckets (whose sum is the economic tab's «da incassare», to the
 * cent), the cash expected by due month, who owes what, and the overdue rows with the
 * reminders that actually left. Every figure is the string the API sent; the bars scale
 * on the server's own `share`.
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
  const expired = data.bands.find((row) => row.code === 'expired')
  const months: BarRow[] = data.by_month.map((row, index) => ({
    label: `${formatIsoMonthYear(row.month)} (${row.number})`,
    value: money(row.amount),
    ratio: row.share,
    tone: (index % 5) + 1,
  }))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Fatture emesse e non incassate al {formatIsoDateItalian(data.today)}, per scadenza.
        </p>
        <Freshness calculatedAt={data.calculated_at} onRefresh={() => void query.refetch()} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <BigNumber label="Da incassare" value={money(data.total)} tone="accent" />
        <BigNumber
          label="Di cui scaduto"
          value={money(expired?.amount ?? '0.00')}
          hint={`${data.total_overdue} ${data.total_overdue === 1 ? 'fattura scaduta' : 'fatture scadute'}`}
        />
      </div>

      <BarRows caption="Scadenziario per fascia" rows={data.bands.map(bandBar)} />
      {expired?.link ? (
        <div className="flex justify-end">
          <Button variant="outline" size="sm" asChild>
            <Link to="/app/invoices" search={{ overdue: true }}>
              Vedi le fatture scadute
            </Link>
          </Button>
        </div>
      ) : null}

      <BarRows caption="Incassi attesi per mese di scadenza" rows={months} />

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Esposizione per cliente</h2>
        {data.by_customer.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura da incassare.</p>
        ) : (
          <Table scrollLabel="Esposizione per cliente">
            <TableHeader>
              <TableRow>
                <TableHead>Cliente</TableHead>
                <TableHead className="text-right">Fatture</TableHead>
                <TableHead className="text-right">Da incassare</TableHead>
                <TableHead className="text-right">Di cui scaduto</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.by_customer.map((row) => (
                <TableRow key={row.customer_id}>
                  <TableCell>
                    <Link
                      to="/app/organizations/$organizationId"
                      params={{ organizationId: row.customer_id }}
                      className="underline-offset-4 hover:underline"
                    >
                      {row.legal_name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right">{row.number}</TableCell>
                  <TableCell className="text-right">{money(row.amount)}</TableCell>
                  <TableCell className="text-right">{money(row.expired)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Fatture scadute</h2>
        {data.overdue.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura scaduta.</p>
        ) : (
          <Table scrollLabel="Fatture scadute">
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
              {data.overdue.map((row) => (
                <TableRow key={row.invoice_id}>
                  <TableCell>
                    <Link
                      to="/app/invoices/$invoiceId"
                      params={{ invoiceId: row.invoice_id }}
                      className="underline-offset-4 hover:underline"
                    >
                      {row.number}
                    </Link>
                  </TableCell>
                  <TableCell>{row.customer}</TableCell>
                  <TableCell>{formatIsoDateItalian(row.due_date)}</TableCell>
                  <TableCell className="text-right">{row.days_of_delay} gg</TableCell>
                  <TableCell className="text-right">{money(row.amount)}</TableCell>
                  <TableCell>{payment_reminders(row.sent_payment_reminders, row.last_payment_reminder_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {data.total_overdue > data.overdue.length ? (
          <p className="text-xs text-muted-foreground">
            Mostrate {data.overdue.length} fatture scadute su {data.total_overdue}.
          </p>
        ) : null}
      </section>
    </div>
  )
}
