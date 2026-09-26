import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { ExternalLink } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { ApiError, matches, type Match, type MatchReport } from '@/lib/api'
import {
  INVOICE_STATE_LABELS,
  PAYMENT_STATE_LABELS,
  formatDay,
  formatDecimal,
  formatMonth,
} from '@/lib/format'
import {
  TUTTO,
  invoiceLabel,
  perMonth,
  perWeek,
  periodInvoices,
  periodMonths,
  progressLine,
  romeToday,
  sliceDays,
} from '@/lib/report'
import { Header } from './lists'

const ROUTE = '/signedIn/admin/matches/$id/report'
const TUTTO_LABEL = "Tutto l'incarico"
const PERIOD_ID = 'consuntivo-periodo'

/** «ACME Srl · Backend developer · lettera n. 2026-001», the card's own title and the
 *  letter it runs on. */
function matchTitle(match: Match): string {
  const numero = match.lettera.numero
  return [match.nome_azienda, match.figura_richiesta, numero ? `lettera n. ${numero}` : null]
    .filter(Boolean)
    .join(' · ')
}

/** Why there is no report, in the API's own sentence: where the link stands for a 409,
 *  the seam's for a 502, the missing configuration for a 503. */
function failure(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Non riesco a leggere il consuntivo.'
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="text-sm font-medium">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
      <Table>{children}</Table>
    </div>
  )
}

function DealLink({ url }: { url: string }) {
  return (
    <Button asChild variant="outline" size="sm">
      {/* The API keeps only an http(s) address the CRM answered for the deal's page. */}
      <a href={url} target="_blank" rel="noreferrer">
        Apri il deal su Pigro
        <ExternalLink className="ml-2 size-3.5" aria-hidden="true" />
      </a>
    </Button>
  )
}

/** The report of one period: the engagement's progress, the period's days, its weeks
 *  and months, and the invoices its days sit on. */
function Report({
  report,
  start,
  period,
  onPeriod,
}: {
  report: MatchReport
  start: string | null
  period: string
  onPeriod: (period: string) => void
}) {
  const months = periodMonths(start, romeToday(), report.per_giorno, period)
  const days = sliceDays(report.per_giorno, period)
  const invoices = periodInvoices(report, period)
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1.5">
          <Label htmlFor={PERIOD_ID}>Periodo</Label>
          <Select value={period} onValueChange={onPeriod}>
            <SelectTrigger id={PERIOD_ID} className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={TUTTO}>{TUTTO_LABEL}</SelectItem>
              {months.map((month) => (
                <SelectItem key={month} value={month}>
                  {formatMonth(month)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {/* The whole engagement against the letter's estimate, whatever the period. */}
        <p className="text-sm font-medium">{progressLine(report)}</p>
      </div>
      {days.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessuna ora in questo periodo.</p>
      ) : (
        <>
          <Section id="consuntivo-giorni" title="Per giorno">
            <Frame>
              <TableHeader>
                <TableRow>
                  <TableHead>Data</TableHead>
                  <TableHead className="text-right">Ore</TableHead>
                  <TableHead>Descrizione</TableHead>
                  <TableHead>Fatture</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {days.map((day) => (
                  <TableRow key={day.data}>
                    <TableCell>{formatDay(day.data, true)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatDecimal(day.ore)}</TableCell>
                    <TableCell className="min-w-64 whitespace-normal">{day.descrizioni.join(' · ')}</TableCell>
                    <TableCell className={day.fatture.length === 0 ? 'text-muted-foreground' : undefined}>
                      {day.fatture.length > 0 ? day.fatture.join(', ') : 'da fatturare'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Frame>
          </Section>
          <div className="grid gap-6 lg:grid-cols-2">
            <Section id="consuntivo-settimane" title="Per settimana">
              <Frame>
                <TableHeader>
                  <TableRow>
                    <TableHead>Settimana</TableHead>
                    <TableHead className="text-right">Ore</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {perWeek(days).map((week) => (
                    <TableRow key={week.settimana}>
                      <TableCell>{`${formatDay(week.da)} – ${formatDay(week.a)}`}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatDecimal(week.ore)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Frame>
            </Section>
            <Section id="consuntivo-mesi" title="Per mese">
              <Frame>
                <TableHeader>
                  <TableRow>
                    <TableHead>Mese</TableHead>
                    <TableHead className="text-right">Ore</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {perMonth(days).map((month) => (
                    <TableRow key={month.mese}>
                      <TableCell>{formatMonth(month.mese)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatDecimal(month.ore)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Frame>
            </Section>
          </div>
        </>
      )}
      <Section id="consuntivo-fatture" title="Fatture">
        {invoices.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura in questo periodo.</p>
        ) : (
          <Frame>
            <TableHeader>
              <TableRow>
                <TableHead>Numero</TableHead>
                <TableHead>Data</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead>Incasso</TableHead>
                <TableHead className="text-right">Ore</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoices.map((invoice, index) => (
                // Two drafts not numbered yet are both «senza numero».
                <TableRow key={`${invoiceLabel(invoice)}-${index}`}>
                  <TableCell className="font-medium">{invoiceLabel(invoice)}</TableCell>
                  <TableCell>{invoice.data ? formatDay(invoice.data) : '—'}</TableCell>
                  <TableCell>{INVOICE_STATE_LABELS[invoice.stato] ?? invoice.stato}</TableCell>
                  <TableCell>{PAYMENT_STATE_LABELS[invoice.stato_pagamento] ?? invoice.stato_pagamento}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatDecimal(invoice.ore)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Frame>
        )}
      </Section>
    </>
  )
}

/**
 * «Consuntivo» (REB-503, spec § 3.5): the hours on a match's deal on Pigro, reached from
 * the match card on «Match e contratti» and from the «Pigro» column of «Match». The
 * report is read once for the whole engagement, from the CRM through the hub and stored
 * nowhere; the period selector, kept in the URL as `?mese=`, slices it here
 * (`lib/report.ts`), so changing the month asks nothing more of the CRM.
 */
export function AdminConsuntivo() {
  const { id } = useParams({ from: ROUTE })
  const search = useSearch({ from: ROUTE })
  const navigate = useNavigate()
  const match = useQuery({ queryKey: ['match', id], queryFn: () => matches.get(id) })
  const report = useQuery({ queryKey: ['match-report', id], queryFn: () => matches.report(id) })
  const period = search.mese ?? romeToday().slice(0, 7)
  const dealUrl = report.data?.pigro_url ?? match.data?.pigro_url ?? null

  function choose(mese: string) {
    void navigate({ to: '/admin/matches/$id/report', params: { id }, search: { mese }, replace: true })
  }

  return (
    <>
      <Header title="Consuntivo">{dealUrl && <DealLink url={dealUrl} />}</Header>
      <div className="space-y-6 px-6 py-6">
        {match.data && <p className="font-medium">{matchTitle(match.data)}</p>}
        {report.isError ? (
          <p className="text-sm text-muted-foreground">{failure(report.error)}</p>
        ) : report.isPending ? (
          <p className="text-sm text-muted-foreground">Caricamento…</p>
        ) : (
          <Report
            report={report.data}
            start={match.data?.lettera_data_inizio ?? null}
            period={period}
            onPeriod={choose}
          />
        )}
      </div>
    </>
  )
}
