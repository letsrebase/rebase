import { Link } from '@tanstack/react-router'
import { RadioGroup } from 'radix-ui'
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
import { BigNumber } from './charts'
import { FiscalPanel } from './FiscalPanel'
import { money } from './format'
import { Freshness } from './Freshness'
import { MonthlyBars } from './MonthlyBars'
import type { Periodo } from './periodo'
import { useEconomicOverview, type FiscalEstimate } from './queries'
import { CASH_BASES, type CashBase } from './search'

/** The year the picker's start date names: cash and taxes are told by the year. A date,
 *  not an amount, so reading it through `Date` is not the coercion criterion 14 bans. */
function yearOf(periodo: Periodo): number {
  return new Date(`${periodo.da}T00:00:00`).getFullYear()
}

function dovuto(estimate: FiscalEstimate | null): string {
  return estimate?.totale_dovuto ? money(estimate.totale_dovuto) : '—'
}

/**
 * `quota` arrives as a plain ratio in [0, 1], the server's own share of the year's
 * *whole* revenue (never of the largest customer, REB-370 §1.5) -- formatting it as a
 * percentage is not the coercion criterion 14 bans: the ratio is already a number when
 * it arrives, the same reasoning `charts.tsx`'s own `widthPercent` relies on to draw a
 * bar from this same kind of field. `Intl.NumberFormat`, not a hand-rolled `toFixed`,
 * for the same `it-IT` locale `money()` already uses.
 */
const shareFormatter = new Intl.NumberFormat('it-IT', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

/**
 * The switch between the two readings of the charts (ORB-133). Radix's radio group, not
 * two buttons: it renders the group and its two `radio`s with the keyboard model that
 * role promises, one tab stop and the arrows moving the choice, which a hand-rolled pair
 * of buttons announced as radios would promise and not keep. The value is the URL's,
 * through the callback, never local state -- a reading held locally is a reading a
 * shared link cannot carry, the same rule as the period (§4).
 */
function BaseSwitch({ base, onChange }: { base: CashBase; onChange: (next: CashBase) => void }) {
  return (
    <RadioGroup.Root
      value={base}
      onValueChange={(next) => onChange(next as CashBase)}
      aria-label="Lettura dei mesi"
      className="flex items-center gap-1"
    >
      {CASH_BASES.map((candidate) => (
        <RadioGroup.Item key={candidate.id} value={candidate.id} asChild>
          <Button variant={candidate.id === base ? 'default' : 'outline'} size="sm">
            {candidate.label}
          </Button>
        </RadioGroup.Item>
      ))}
    </RadioGroup.Root>
  )
}

export function EconomicTab({
  periodo,
  base,
  onBaseChange,
}: {
  periodo: Periodo
  base: CashBase
  onBaseChange: (next: CashBase) => void
}) {
  const anno = yearOf(periodo)
  const query = useEconomicOverview(anno, base)

  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  const {
    cassa,
    fiscale,
    fiscale_proiettato,
    netto_effettivo,
    netto_proiettato,
    concentrazione_clienti,
  } = query.data
  // Named from the server's echo, not from the URL: the sentence describes what was
  // drawn. Through the label table, because the wire value is a contract and not copy.
  const lettura = CASH_BASES.find((candidate) => candidate.id === cassa.base)?.phrase ?? ''

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {/* The reading is named in words as well as on the switch: the sentence is what
              a screenshot carries, and «per competenza» is the difference between a
              chart of what was earned and a chart of what arrived. */}
          Vista economica {anno} {lettura}: ricavi, costi passivi e stima fiscale.
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <BaseSwitch base={base} onChange={onBaseChange} />
          <Freshness
            calcolatoAlle={query.data.calcolato_alle}
            onRefresh={() => void query.refetch()}
          />
        </div>
      </div>

      {/* The two charts come before the figures (2026-09-09): the shape of the year is
          what the eye reads first, and the cards below give it its exact numbers. */}
      <MonthlyBars
        title={`Andamento economico ${anno}`}
        months={cassa.mesi}
        series={['incassato', 'costi']}
        shares="quote_andamento"
      />
      <MonthlyBars
        title={`Proiezione economica ${anno}`}
        months={cassa.mesi}
        series={['incassato', 'da_incassare', 'bozze', 'costi']}
        shares="quote_proiezione"
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <BigNumber label="Ricavi incassati" value={money(cassa.incassato)} tone="accent" />
        <BigNumber
          label="Ricavi proiettati"
          value={money(cassa.proiettato)}
          hint={`Incassato ${money(cassa.incassato)} · da incassare ${money(cassa.da_incassare)} · bozze e proforma ${money(cassa.bozze)}`}
        />
        <BigNumber label="Costi passivi" value={money(cassa.costi)} />
        <BigNumber
          label="Totale lordo effettivo"
          value={money(cassa.lordo_effettivo)}
          hint="incassato meno costi"
        />
        {fiscale ? (
          <>
            <BigNumber
              label="Imponibile forfettario stimato"
              value={money(fiscale.imponibile ?? '0.00')}
              hint={
                fiscale_proiettato?.imponibile
                  ? `con proiezione ${money(fiscale_proiettato.imponibile)}`
                  : undefined
              }
            />
            <BigNumber
              label="Totale da saldare"
              value={dovuto(fiscale)}
              hint={`imposta sostitutiva ${money(fiscale.imposta_sostitutiva ?? '0.00')} · INPS ${money(fiscale.contributi ?? '0.00')}`}
            />
            {/* The revenue the estimate was actually computed on, from the estimate
                itself: under the accrual reading `cassa.proiettato` can be a different
                figure (a document whose period and payment fall in different years). */}
            <BigNumber
              label="Totale da saldare con proiezione"
              value={dovuto(fiscale_proiettato)}
              hint={
                fiscale_proiettato
                  ? `su ricavi proiettati ${money(fiscale_proiettato.ricavi)}, su base incasso`
                  : undefined
              }
            />
            {/* The two nets are on the money whatever the switch says (the forfettario is
                taxed on what was collected in the year; DECISIONS.md, 2026-09-10), so the
                hints name the base instead of pointing at a lordo card that, under the
                accrual reading, may be a different figure. */}
            <BigNumber
              label="Totale netto ricavi"
              value={netto_effettivo ? money(netto_effettivo) : '—'}
              hint="incassato meno costi e tasse stimate, su base incasso"
            />
            <BigNumber
              label="Totale netto ricavi con proiezione"
              value={netto_proiettato ? money(netto_proiettato) : '—'}
              hint="proiettato meno costi e tasse stimate, su base incasso"
            />
          </>
        ) : (
          <div className="border bg-card p-4 sm:col-span-2">
            <p className="text-sm text-muted-foreground">Stima fiscale</p>
            {/* No link out any more: the card below says which of the two is missing, in
                the server's own words, and offers the settings screen when that is the
                answer. */}
            <p className="mt-1 text-sm">
              Non disponibile qui: serve un profilo fiscale configurato e un account
              amministratore.
            </p>
          </div>
        )}
      </div>

      {/* Whole-practice, calendar-year concentration (§1.5, REB-370): each customer's
          own share of the year's *whole* invoiced revenue, never of the largest
          customer's own figure -- deliberately not `per_cliente` on the receivables
          page, which answers "who currently owes the most" against an outstanding
          balance and is a different chart over a different question. Open to every
          role, unlike the fiscal cards above it, so it sits outside the `fiscale`
          branch and always renders. */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium">Concentrazione clienti</h2>
        <p className="text-sm text-muted-foreground">
          La quota di ciascun cliente sui ricavi fatturati nell'anno -- non
          l'esposizione residua di «Da incassare» nella scheda scadenze, che risponde a
          una domanda diversa (chi deve di più oggi).
        </p>
        {concentrazione_clienti.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura emessa nell'anno.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cliente</TableHead>
                <TableHead className="text-right">Fatture</TableHead>
                <TableHead className="text-right">Ricavi</TableHead>
                <TableHead className="text-right">Quota</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {concentrazione_clienti.map((row) => (
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
                  <TableCell className="text-right">{row.fatture}</TableCell>
                  <TableCell className="text-right">{money(row.ricavi)}</TableCell>
                  <TableCell className="text-right">{shareFormatter.format(row.quota)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      {/* The whole estimate, immediately under the figures it explains: the cards say how
          much is owed, this says out of what and at which rates, so it belongs against
          them and not at the foot of the page. That the charts now come *first* is a
          separate decision (2026-09-09: the shape of the year is read before its exact
          numbers) and it does not move this card -- what the spec asked for is the card
          against the figures, wherever the charts end up. It reads the year on its own --
          one request, its own loading and error branches -- rather than taking the excerpt
          `panoramica` already carries, because a card that renders half of itself while the
          other half loads is worse than a card that arrives whole. */}
      <FiscalPanel anno={anno} />
    </div>
  )
}
