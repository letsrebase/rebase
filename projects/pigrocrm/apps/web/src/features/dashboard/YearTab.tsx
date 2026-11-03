import { Link } from '@tanstack/react-router'
import { Download } from 'lucide-react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { useLegalEntity } from '@/lib/legalEntity'
import { toProblem } from '@/lib/api'
import { formatIsoDateItalian } from '@/lib/dates'
import { DownloadButton } from '@/components/DownloadButton'
import { Skeleton } from '@rebase/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { BigNumber } from './charts'
import { money } from './format'
import { useYearOverview, yearCsvPath, type YearOverview, type YearRegisterRow } from './queries'
import type { Period } from './period'

/** The year the picker's start date names, the same reading the economic tab has: a
 *  date, not an amount, so going through `Date` is not the coercion criterion 14 bans. */
function yearOf(period: Period): number {
  return new Date(`${period.from}T00:00:00`).getFullYear()
}

/** The regime an invoice froze at issue, in the words the settings screen uses. A code
 *  this product has no name for is printed as it is rather than guessed at. */
function regimeLabel(code: string | null, pack: string): string {
  if (code === 'RF19') return 'Forfettario (RF19)'
  if (code === 'RF01') return 'Ordinario (RF01)'
  if (code === null && pack === 'non-it') return 'Estero'
  return code ?? 'Non indicato'
}

/** «0,00» as a rate: the API's decimal with its comma, no coercion. */
function rate(value: string): string {
  return `${value.replace('.', ',')}%`
}

/** The stamp-duty sentences the invoices print, each with how many carry it. Grouping the
 *  words is presentation; the sentence itself is the server's, as the document has it. */
function stampDutyDeclarations(register: YearRegisterRow[]): { sentence: string; invoices: number }[] {
  const counts = new Map<string, number>()
  for (const row of register) {
    if (row.stamp_duty_declaration) {
      counts.set(row.stamp_duty_declaration, (counts.get(row.stamp_duty_declaration) ?? 0) + 1)
    }
  }
  return [...counts].map(([sentence, invoices]) => ({ sentence, invoices }))
}

function Register({ overview }: { overview: YearOverview }) {
  const { register = [], total } = overview
  if (register.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">Nessuna fattura emessa nel {overview.year}.</p>
    )
  }
  const declarations = stampDutyDeclarations(register)
  return (
    <div className="space-y-3">
      <Table scrollLabel="Registro delle fatture emesse">
        <TableHeader>
          <TableRow>
            <TableHead>Numero</TableHead>
            <TableHead>Data</TableHead>
            <TableHead>Cliente</TableHead>
            <TableHead className="text-right">Imponibile</TableHead>
            <TableHead className="text-right">di cui rivalsa INPS</TableHead>
            <TableHead className="text-right">Bollo</TableHead>
            <TableHead className="text-right">Totale</TableHead>
            <TableHead>Incasso</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {register.map((row) => (
            <TableRow key={row.invoice_id}>
              <TableCell className="tabular-nums">
                <Link
                  to="/app/invoices/$invoiceId"
                  params={{ invoiceId: row.invoice_id }}
                  className="underline-offset-4 hover:underline"
                >
                  {row.year}/{row.number}
                </Link>
              </TableCell>
              <TableCell className="tabular-nums">{formatIsoDateItalian(row.issue_date)}</TableCell>
              <TableCell>
                <Link
                  to="/app/customers/$customerId"
                  params={{ customerId: row.customer_id }}
                  className="underline-offset-4 hover:underline"
                >
                  {row.legal_name}
                </Link>
                {row.vat_number ? (
                  <span className="block text-xs text-muted-foreground">P.IVA {row.vat_number}</span>
                ) : null}
              </TableCell>
              <TableCell className="text-right tabular-nums">{money(row.taxable_amount)}</TableCell>
              <TableCell className="text-right tabular-nums">{money(row.social_security_surcharge)}</TableCell>
              <TableCell className="text-right tabular-nums">{money(row.stamp_duty)}</TableCell>
              <TableCell className="text-right tabular-nums">{money(row.total)}</TableCell>
              <TableCell>
                {row.payment_status === 'paid'
                  ? row.payment_date
                    ? `Incassata il ${formatIsoDateItalian(row.payment_date)}`
                    : 'Incassata'
                  : 'Da incassare'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell colSpan={3} className="font-semibold">
              Totale registro · {total.invoices} {total.invoices === 1 ? 'invoice' : 'invoices'}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(total.taxable_amount)}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(total.social_security_surcharge)}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(total.stamp_duty)}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(total.total)}
            </TableCell>
            <TableCell />
          </TableRow>
        </TableFooter>
      </Table>
      {declarations.length > 0 ? (
        <div className="space-y-1 text-xs text-muted-foreground">
          <p>Il bollo è assolto in modo virtuale, come dichiara ogni fattura:</p>
          <ul className="space-y-0.5">
            {declarations.map((declaration) => (
              <li key={declaration.sentence}>
                «{declaration.sentence}»{' '}
                <span className="tabular-nums">
                  ({declaration.invoices} {declaration.invoices === 1 ? 'invoice' : 'invoices'})
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

/**
 * One legal entity's year as the accountant reads it (REB-757): the revenue by regime and by
 * tax treatment, the register of the issued invoices with the stamp duty and the surcharge
 * apart, and the CSV of the same lines.
 *
 * The register year is the year the invoices were numbered in (`year`), the universe the
 * dashboard's own revenue figure sums, so the total below is that figure and the CSV
 * totals to it. The legal references and the stamp-duty sentence are the lines' and the
 * invoices' own words, printed as stored: rewording them here would put on a page the
 * accountant reads a sentence no document ever carried.
 *
 * `quiet` is «tutte» with several legal entities, where the tab draws one block per legal entity: one
 * the API has no register for (404) draws nothing, and one with nothing issued says so
 * under its own name, so the tab is never blank.
 */
export function YearPanel({
  year,
  legalEntityId,
  title,
  quiet = false,
}: {
  year: number
  legalEntityId?: string
  title?: string
  quiet?: boolean
}) {
  const { selected } = useLegalEntity()
  const query = useYearOverview(year, legalEntityId)

  if (query.isError) {
    if (quiet && toProblem(query.error).status === 404) return null
    return <QueryErrorBanner error={query.error} />
  }
  if (query.isPending || !query.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }
  const overview = query.data
  // The azienda the file is of: the block's own, else the one the sidebar selected. The
  // server resolves the only legal entity of a one-legal-entity space by itself, so nothing is sent.
  const csvLegalEntity = legalEntityId ?? selected

  return (
    <section className="space-y-6" aria-label={`Anno ${year}${title ? ` · ${title}` : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {title ? <strong className="mr-2 font-semibold text-foreground">{title}</strong> : null}
          Registro {year}: le fatture emesse nell’anno, con il bollo e la rivalsa a parte.
          {overview.cancelled > 0
            ? ` ${overview.cancelled === 1 ? 'Una fattura annullata resta fuori' : `${overview.cancelled} fatture annullate restano fuori`} dai totali.`
            : ''}
        </p>
        <DownloadButton variant="outline" path={yearCsvPath(year, csvLegalEntity)}>
          <Download className="mr-2 size-4" aria-hidden="true" />
          Scarica il CSV per il commercialista
        </DownloadButton>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <BigNumber
          label="Ricavi dell’anno"
          value={money(overview.total.taxable_amount)}
          tone="accent"
          hint={`${overview.total.invoices} ${overview.total.invoices === 1 ? 'fattura emessa' : 'fatture emesse'}`}
        />
        <BigNumber
          label="di cui rivalsa INPS"
          value={money(overview.total.social_security_surcharge)}
          hint="già nei ricavi"
        />
        <BigNumber
          label="Bollo virtuale"
          value={money(overview.total.stamp_duty)}
          hint="a carico dell’emittente, fuori dai ricavi"
        />
        <BigNumber label="Totale documenti" value={money(overview.total.total)} />
      </div>

      {overview.regimes.length > 0 ? (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Ricavi per regime</h2>
          <p className="text-sm text-muted-foreground">
            Il regime di ogni fattura è quello congelato al momento dell’emissione.
          </p>
          <Table scrollLabel="Ricavi per regime">
            <TableHeader>
              <TableRow>
                <TableHead>Regime</TableHead>
                <TableHead className="text-right">Fatture</TableHead>
                <TableHead className="text-right">Ricavi</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.regimes.map((row) => (
                <TableRow key={`${row.regime_code ?? ''}:${row.pack_id}`}>
                  <TableCell>{regimeLabel(row.regime_code, row.pack_id)}</TableCell>
                  <TableCell className="text-right">{row.invoices}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.revenue)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      ) : null}

      {overview.treatments.length > 0 ? (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Ricavi per trattamento fiscale</h2>
          <p className="text-sm text-muted-foreground">
            Aliquota, natura e riferimento normativo come sono stampati sulle righe.
          </p>
          <Table scrollLabel="Ricavi per trattamento fiscale">
            <TableHeader>
              <TableRow>
                <TableHead>Aliquota</TableHead>
                <TableHead>Natura</TableHead>
                <TableHead>Riferimento normativo</TableHead>
                <TableHead className="text-right">Righe</TableHead>
                <TableHead className="text-right">Ricavi</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.treatments.map((row) => (
                <TableRow
                  key={`${row.vat_rate}:${row.vat_nature ?? ''}:${row.legal_reference ?? ''}`}
                >
                  <TableCell className="tabular-nums">{rate(row.vat_rate)}</TableCell>
                  <TableCell>{row.vat_nature ?? '—'}</TableCell>
                  <TableCell className="max-w-md whitespace-normal">
                    {row.legal_reference ?? '—'}
                  </TableCell>
                  <TableCell className="text-right">{row.lines}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.revenue)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Registro delle fatture emesse</h2>
        <Register overview={overview} />
      </section>
    </section>
  )
}

/**
 * The «Anno» tab: the year of the period picker, one block per legal entity when the sidebar
 * says «Tutte» on a space with several (a register is numbered per legal entity, and two
 * legal entities' invoices are never in one file).
 */
export function YearTab({ period }: { period: Period }) {
  const year = yearOf(period)
  const { legal_entities, selected, several } = useLegalEntity()
  const byLegalEntity = several && selected === null

  return (
    <div className="space-y-8">
      {byLegalEntity ? (
        legal_entities.map((legal_entity) => (
          <YearPanel
            key={legal_entity.id}
            year={year}
            legalEntityId={legal_entity.id}
            title={legal_entity.name}
            quiet
          />
        ))
      ) : (
        <YearPanel year={year} />
      )}
    </div>
  )
}
