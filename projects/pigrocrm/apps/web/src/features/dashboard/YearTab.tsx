import { Link } from '@tanstack/react-router'
import { Download } from 'lucide-react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { useLegalEntity } from '@/lib/legalEntity'
import { toProblem } from '@/lib/api'
import { formatIsoDateItalian } from '@/lib/dates'
import { Button } from '@rebase/ui/button'
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
import { useYearOverview, yearCsvUrl, type YearOverview, type YearRegisterRow } from './queries'
import type { Periodo } from './periodo'

/** The year the picker's start date names, the same reading the economic tab has: a
 *  date, not an amount, so going through `Date` is not the coercion criterion 14 bans. */
function yearOf(periodo: Periodo): number {
  return new Date(`${periodo.da}T00:00:00`).getFullYear()
}

/** The regime an invoice froze at issue, in the words the settings screen uses. A code
 *  this product has no name for is printed as it is rather than guessed at. */
function regimeLabel(codice: string | null, pack: string): string {
  if (codice === 'RF19') return 'Forfettario (RF19)'
  if (codice === 'RF01') return 'Ordinario (RF01)'
  if (codice === null && pack === 'non-it') return 'Estero'
  return codice ?? 'Non indicato'
}

/** «0,00» as a rate: the API's decimal with its comma, no coercion. */
function rate(value: string): string {
  return `${value.replace('.', ',')}%`
}

/** The stamp-duty sentences the invoices print, each with how many carry it. Grouping the
 *  words is presentation; the sentence itself is the server's, as the document has it. */
function bolloDeclarations(registro: YearRegisterRow[]): { sentence: string; fatture: number }[] {
  const counts = new Map<string, number>()
  for (const row of registro) {
    if (row.dichiarazione_bollo) {
      counts.set(row.dichiarazione_bollo, (counts.get(row.dichiarazione_bollo) ?? 0) + 1)
    }
  }
  return [...counts].map(([sentence, fatture]) => ({ sentence, fatture }))
}

function Registro({ overview }: { overview: YearOverview }) {
  const { registro, totali } = overview
  if (registro.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">Nessuna fattura emessa nel {overview.anno}.</p>
    )
  }
  const declarations = bolloDeclarations(registro)
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
          {registro.map((row) => (
            <TableRow key={row.invoice_id}>
              <TableCell className="tabular-nums">
                <Link
                  to="/app/invoices/$invoiceId"
                  params={{ invoiceId: row.invoice_id }}
                  className="underline-offset-4 hover:underline"
                >
                  {row.anno}/{row.numero}
                </Link>
              </TableCell>
              <TableCell className="tabular-nums">{formatIsoDateItalian(row.data_emissione)}</TableCell>
              <TableCell>
                <Link
                  to="/app/customers/$customerId"
                  params={{ customerId: row.customer_id }}
                  className="underline-offset-4 hover:underline"
                >
                  {row.ragione_sociale}
                </Link>
                {row.partita_iva ? (
                  <span className="block text-xs text-muted-foreground">P.IVA {row.partita_iva}</span>
                ) : null}
              </TableCell>
              <TableCell className="text-right tabular-nums">{money(row.imponibile)}</TableCell>
              <TableCell className="text-right tabular-nums">{money(row.rivalsa)}</TableCell>
              <TableCell className="text-right tabular-nums">{money(row.bollo)}</TableCell>
              <TableCell className="text-right tabular-nums">{money(row.totale)}</TableCell>
              <TableCell>
                {row.stato_pagamento === 'incassato'
                  ? row.data_incasso
                    ? `Incassata il ${formatIsoDateItalian(row.data_incasso)}`
                    : 'Incassata'
                  : 'Da incassare'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell colSpan={3} className="font-semibold">
              Totale registro · {totali.fatture} {totali.fatture === 1 ? 'fattura' : 'fatture'}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(totali.imponibile)}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(totali.rivalsa)}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(totali.bollo)}
            </TableCell>
            <TableCell className="text-right font-semibold tabular-nums">
              {money(totali.totale)}
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
                  ({declaration.fatture} {declaration.fatture === 1 ? 'fattura' : 'fatture'})
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
 * One azienda's year as the accountant reads it (REB-757): the revenue by regime and by
 * tax treatment, the register of the issued invoices with the stamp duty and the rivalsa
 * apart, and the CSV of the same lines.
 *
 * The register year is the year the invoices were numbered in (`anno`), the universe the
 * dashboard's own revenue figure sums, so the total below is that figure and the CSV
 * totals to it. The legal references and the stamp-duty sentence are the lines' and the
 * invoices' own words, printed as stored: rewording them here would put on a page the
 * accountant reads a sentence no document ever carried.
 *
 * `quiet` is «tutte» with several aziende, where the tab draws one block per azienda: one
 * the API has no register for (404) draws nothing, and one with nothing issued says so
 * under its own name, so the tab is never blank.
 */
export function YearPanel({
  anno,
  aziendaId,
  titolo,
  quiet = false,
}: {
  anno: number
  aziendaId?: string
  titolo?: string
  quiet?: boolean
}) {
  const { selected } = useLegalEntity()
  const query = useYearOverview(anno, aziendaId)

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
  // server resolves the only azienda of a one-azienda space by itself, so nothing is sent.
  const csvAzienda = aziendaId ?? selected

  return (
    <section className="space-y-6" aria-label={`Anno ${anno}${titolo ? ` · ${titolo}` : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {titolo ? <strong className="mr-2 font-semibold text-foreground">{titolo}</strong> : null}
          Registro {anno}: le fatture emesse nell’anno, con il bollo e la rivalsa a parte.
          {overview.annullate > 0
            ? ` ${overview.annullate === 1 ? 'Una fattura annullata resta fuori' : `${overview.annullate} fatture annullate restano fuori`} dai totali.`
            : ''}
        </p>
        <Button asChild variant="outline">
          <a
            // Snyk Code javascript/DOMXSS here is a false positive: always a same-origin
            // path under /api/analytics/, the query built with URLSearchParams.
            href={yearCsvUrl(anno, csvAzienda)}
          >
            <Download className="mr-2 size-4" aria-hidden="true" />
            Scarica il CSV per il commercialista
          </a>
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <BigNumber
          label="Ricavi dell’anno"
          value={money(overview.totali.imponibile)}
          tone="accent"
          hint={`${overview.totali.fatture} ${overview.totali.fatture === 1 ? 'fattura emessa' : 'fatture emesse'}`}
        />
        <BigNumber
          label="di cui rivalsa INPS"
          value={money(overview.totali.rivalsa)}
          hint="già nei ricavi"
        />
        <BigNumber
          label="Bollo virtuale"
          value={money(overview.totali.bollo)}
          hint="a carico dell’emittente, fuori dai ricavi"
        />
        <BigNumber label="Totale documenti" value={money(overview.totali.totale)} />
      </div>

      {overview.regimi.length > 0 ? (
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
              {overview.regimi.map((row) => (
                <TableRow key={`${row.codice_regime ?? ''}:${row.pack_id}`}>
                  <TableCell>{regimeLabel(row.codice_regime, row.pack_id)}</TableCell>
                  <TableCell className="text-right">{row.fatture}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.ricavi)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      ) : null}

      {overview.trattamenti.length > 0 ? (
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
              {overview.trattamenti.map((row) => (
                <TableRow
                  key={`${row.aliquota_iva}:${row.natura ?? ''}:${row.riferimento_normativo ?? ''}`}
                >
                  <TableCell className="tabular-nums">{rate(row.aliquota_iva)}</TableCell>
                  <TableCell>{row.natura ?? '—'}</TableCell>
                  <TableCell className="max-w-md whitespace-normal">
                    {row.riferimento_normativo ?? '—'}
                  </TableCell>
                  <TableCell className="text-right">{row.righe}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.ricavi)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Registro delle fatture emesse</h2>
        <Registro overview={overview} />
      </section>
    </section>
  )
}

/**
 * The «Anno» tab: the year of the period picker, one block per azienda when the sidebar
 * says «Tutte» on a space with several (a register is numbered per azienda, and two
 * aziende's invoices are never in one file).
 */
export function YearTab({ periodo }: { periodo: Periodo }) {
  const anno = yearOf(periodo)
  const { aziende, selected, several } = useLegalEntity()
  const perAzienda = several && selected === null

  return (
    <div className="space-y-8">
      {perAzienda ? (
        aziende.map((azienda) => (
          <YearPanel
            key={azienda.id}
            anno={anno}
            aziendaId={azienda.id}
            titolo={azienda.nome}
            quiet
          />
        ))
      ) : (
        <YearPanel anno={anno} />
      )}
    </div>
  )
}
