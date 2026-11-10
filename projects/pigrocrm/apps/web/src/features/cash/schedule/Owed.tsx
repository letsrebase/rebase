import type { ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { BarRows, BigNumber, type BarRow } from '@/features/dashboard/charts'
import { money } from '@/features/dashboard/format'
import { useReceivablesDashboard, type BandDueDate } from '@/features/dashboard/queries'
import { formatIsoDateItalian } from '@/lib/dates'
import { LATE_LISTS_ID } from './Late'
import { ROW_TONE, lateLabel } from './labels'
import { usePayablesDashboard } from './queries'

/** The overdue band is the one outcome among six steps: the destructive tint the pipeline card
 *  already uses for «Perso», and no sixth colour. */
const EXPIRED_FILL = 'color-mix(in oklab, var(--destructive) 10%, transparent)'

function bandBar(row: BandDueDate, index: number): BarRow {
  return {
    label: row.number > 0 ? `${row.label} (${row.number})` : row.label,
    value: money(row.amount),
    // The share is the server's (`share`, in [0, 1]); nothing here divides an amount.
    ratio: row.share,
    tone: index + 1,
    color: row.code === 'expired' ? EXPIRED_FILL : undefined,
  }
}

function reminders(sent: number, last: string | null): string {
  if (sent === 0) return 'nessuno'
  const count = sent === 1 ? '1 sollecito' : `${sent} solleciti`
  return last ? `${count}, ultimo il ${formatIsoDateItalian(last)}` : count
}

interface OwedProps {
  title: string
  today: string
  total: string
  bands: BandDueDate[]
  totalOverdue: number
  shownOverdue: number
  noun: { one: string; many: string }
  caption: string
  children: ReactNode
}

/** One of the two sections «Da incassare» / «Da pagare»: the big number, «Di cui scaduto», the
 *  six bands and the late table the caller passes in. */
function OwedSection({
  title,
  today,
  total,
  bands,
  totalOverdue,
  shownOverdue,
  noun,
  caption,
  children,
}: OwedProps) {
  const expired = bands.find((band) => band.code === 'expired')
  return (
    <section aria-label={title} className="min-w-0 space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <BigNumber label={title} value={money(total)} hint={`Al ${formatIsoDateItalian(today)}, per scadenza`} />
        <BigNumber
          label="Di cui scaduto"
          value={money(expired?.amount ?? '0.00')}
          hint={`${totalOverdue} ${totalOverdue === 1 ? noun.one : noun.many}`}
        />
      </div>
      <BarRows caption={caption} rows={bands.map(bandBar)} />
      {children}
      {totalOverdue > shownOverdue ? (
        <p className="text-xs text-muted-foreground">
          Mostrate {shownOverdue} su {totalOverdue}.
        </p>
      ) : null}
    </section>
  )
}

/** «Da incassare»: the receivables, with the «Solleciti» column the payables do not have. */
export function ReceivablesSection() {
  const query = useReceivablesDashboard()
  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) return <LoadingState label="Leggo le fatture da incassare…" />
  const data = query.data
  return (
    <OwedSection
      title="Da incassare"
      today={data.today}
      total={data.total}
      bands={data.bands}
      totalOverdue={data.total_overdue}
      shownOverdue={data.overdue.length}
      noun={{ one: 'fattura scaduta', many: 'fatture scadute' }}
      caption="Da incassare per fascia"
    >
      <div id={LATE_LISTS_ID} className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium">Fatture scadute da incassare</h3>
          {data.bands.find((band) => band.code === 'expired')?.link ? (
            <Button variant="outline" size="sm" asChild>
              <Link to="/app/invoices" search={{ overdue: true }}>
                Vedi le fatture scadute
              </Link>
            </Button>
          ) : null}
        </div>
        {data.overdue.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura scaduta.</p>
        ) : (
          <Table scrollLabel="Fatture scadute da incassare">
            <TableHeader>
              <TableRow>
                <TableHead>Fattura</TableHead>
                <TableHead>Scadenza</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead className="text-right">Importo</TableHead>
                <TableHead>Solleciti</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.overdue.map((row) => (
                <TableRow key={row.invoice_id}>
                  <TableCell className="max-w-56">
                    <Link
                      to="/app/invoices/$invoiceId"
                      params={{ invoiceId: row.invoice_id }}
                      className="block underline-offset-4 hover:underline"
                    >
                      {row.number}
                    </Link>
                    <span className="block truncate text-xs text-muted-foreground" title={row.customer}>
                      {row.customer}
                    </span>
                  </TableCell>
                  <TableCell className="tabular-nums">{formatIsoDateItalian(row.due_date)}</TableCell>
                  <TableCell>
                    <StatusPill tone={ROW_TONE.overdue}>{lateLabel(row.days_of_delay)}</StatusPill>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.amount)}</TableCell>
                  <TableCell>{reminders(row.sent_payment_reminders, row.last_payment_reminder_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </OwedSection>
  )
}

/** «Da pagare»: the mirror, over money going out, with no reminder column. */
export function PayablesSection() {
  const query = usePayablesDashboard()
  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) return <LoadingState label="Leggo le fatture da pagare…" />
  const data = query.data
  return (
    <OwedSection
      title="Da pagare"
      today={data.today}
      total={data.total}
      bands={data.bands}
      totalOverdue={data.total_overdue}
      shownOverdue={data.overdue.length}
      noun={{ one: 'voce scaduta', many: 'voci scadute' }}
      caption="Da pagare per fascia"
    >
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Voci scadute da pagare</h3>
        {data.overdue.length === 0 ? (
          <p className="text-sm text-muted-foreground">Niente di scaduto da pagare.</p>
        ) : (
          <Table scrollLabel="Voci scadute da pagare">
            <TableHeader>
              <TableRow>
                <TableHead>Fornitore</TableHead>
                <TableHead>Scadenza</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead className="text-right">Importo</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.overdue.map((row) => (
                <TableRow key={row.due_item_id}>
                  <TableCell className="max-w-56">
                    <span className="block truncate" title={row.counterparty ?? undefined}>
                      {row.counterparty ?? row.description ?? 'Senza nome'}
                    </span>
                    {row.counterparty && row.description && (
                      <span className="block truncate text-xs text-muted-foreground" title={row.description}>
                        {row.description}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="tabular-nums">{formatIsoDateItalian(row.due_date)}</TableCell>
                  <TableCell>
                    <StatusPill tone={ROW_TONE.overdue}>{lateLabel(row.days_of_delay)}</StatusPill>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.amount)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </OwedSection>
  )
}
