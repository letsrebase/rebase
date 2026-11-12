import { Link } from '@tanstack/react-router'
import { RadioGroup } from 'radix-ui'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { NOT_COMPUTABLE, formatPercent } from '@/features/analytics/format'
import { useLegalEntity } from '@/lib/legalEntity'
import { useModules } from '@/lib/modules'
import { Button } from '@rebase/ui/button'
import { LoadingState } from '@/components/LoadingState'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { BigNumber } from './charts'
import { CeilingPanel } from './CeilingPanel'
import { FiscalPanel } from './FiscalPanel'
import { money } from './format'
import { Freshness } from './Freshness'
import { MonthlyBars } from './MonthlyBars'
import type { Period } from './period'
import { useEconomicOverview, type EconomicOverview, type FiscalEstimate } from './queries'
import { CASH_BASES, type CashBase } from './search'

/** The year the picker's start date names: cash and taxes are told by the year. A date,
 *  not an amount, so reading it through `Date` is not the coercion criterion 14 bans. */
function yearOf(period: Period): number {
  return new Date(`${period.from}T00:00:00`).getFullYear()
}

function due(estimate: FiscalEstimate | null): string {
  return estimate?.due_total ? money(estimate.due_total) : '—'
}

/** A line of the estimate, or «non calcolabile» when its parameter is missing: the
 *  substitute tax needs the INPS rate too (it is owed on the income less the
 *  contributions), and a `0,00 €` would read as «you owe nothing». */
function estimated(value: string | null): string {
  return value === null ? NOT_COMPUTABLE : money(value)
}

/** The IRAP card's hint: its rate, or how many regions it is split over (no one rate then, REB-1092). */
function irapHint(irap: NonNullable<FiscalEstimate['company']>['irap']): string {
  const regions = irap.regions?.length ?? 0
  return regions > 0 ? `ripartita su ${regions} regioni` : `aliquota ${formatPercent(irap.rate)}`
}

/** What the opening sentence says the tab shows, for the modules that are on. */
function shownFigures(purchasesOn: boolean, taxesOn: boolean): string {
  if (purchasesOn && taxesOn) return 'ricavi, costi passivi e stima fiscale'
  if (purchasesOn) return 'ricavi e costi passivi'
  if (taxesOn) return 'ricavi e stima fiscale'
  return 'ricavi'
}

type ConcentrationRow = EconomicOverview['customer_concentration'][number]

/**
 * The rows grouped by the legal entity whose revenue each share is of (REB-632, spec §1.9),
 * in the order the server sent them, which is the selector's: the default first. A
 * customer billed by two legal entities appears in both groups, once per share.
 */
function byLegalEntity(rows: ConcentrationRow[]): { legalEntityId: string; rows: ConcentrationRow[] }[] {
  const groups: { legalEntityId: string; rows: ConcentrationRow[] }[] = []
  for (const row of rows) {
    const last = groups[groups.length - 1]
    if (last && last.legalEntityId === row.legal_entity_id) last.rows.push(row)
    else groups.push({ legalEntityId: row.legal_entity_id, rows: [row] })
  }
  return groups
}

function ConcentrationTable({
  rows,
  label = 'Concentrazione dei ricavi per cliente',
}: {
  rows: ConcentrationRow[]
  label?: string
}) {
  return (
    <Table scrollLabel={label}>
      <TableHeader>
        <TableRow>
          <TableHead>Cliente</TableHead>
          <TableHead className="text-right">Fatture</TableHead>
          <TableHead className="text-right">Ricavi</TableHead>
          <TableHead className="text-right">Quota</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={`${row.legal_entity_id}:${row.customer_id}`}>
            <TableCell>
              <Link
                to="/app/organizations/$organizationId"
                params={{ organizationId: row.customer_id }}
                className="underline-offset-4 hover:underline"
              >
                {row.legal_name}
              </Link>
            </TableCell>
            <TableCell className="text-right">{row.invoices}</TableCell>
            <TableCell className="text-right">{money(row.revenue)}</TableCell>
            <TableCell className="text-right">{shareFormatter.format(row.share)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/**
 * `share` arrives as a plain ratio in [0, 1], the server's own share of the year's
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
  period,
  base,
  onBaseChange,
}: {
  period: Period
  base: CashBase
  onBaseChange: (next: CashBase) => void
}) {
  const year = yearOf(period)
  const query = useEconomicOverview(year, base)
  // «Tutte» on a space with several legal entities (REB-632, spec §1.9): the cash adds up on
  // the server, the taxes do not, so the tab draws the estimate once per legal entity and
  // names which legal entity each concentration share belongs to. With one legal entity, or one
  // selected, the page reads as it always did.
  const { legal_entities, selected, several, byId } = useLegalEntity()
  const perLegalEntity = several && selected === null
  // What the tab shows is what its switched-on modules feed it (spec 2026-11-09, B5): the
  // fiscal estimate, the ceilings and the nets drawn from it are `taxes`, the passive
  // costs are `purchases`. The figures stay in the books and return with the module.
  const modules = useModules()
  const taxesOn = modules.on('taxes')
  const purchasesOn = modules.on('purchases')

  if (query.isError) return <QueryErrorBanner error={query.error} />
  // Not before the modules are known either: the panels of `taxes` would otherwise be drawn,
  // and ask the API for what a switched-off module refuses, for the length of one read.
  if (query.isPending || !query.data || modules.loading) {
    return <LoadingState />
  }

  const {
    pension_fund,
    tax,
    projected_tax,
    actual_net,
    projected_net,
    customer_concentration,
  } = query.data
  // Named from the server's echo, not from the URL: the sentence describes what was
  // drawn. Through the label table, because the wire value is a contract and not copy.
  const reading = CASH_BASES.find((candidate) => candidate.id === pension_fund.base)?.phrase ?? ''

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {/* The reading is named in words as well as on the switch: the sentence is what
              a screenshot carries, and «per competenza» is the difference between a
              chart of what was earned and a chart of what arrived. */}
          Vista economica {year} {reading}: {shownFigures(purchasesOn, taxesOn)}.
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <BaseSwitch base={base} onChange={onBaseChange} />
          <Freshness
            calculatedAt={query.data.calculated_at}
            onRefresh={() => void query.refetch()}
          />
        </div>
      </div>

      {/* The two charts come before the figures (2026-09-09): the shape of the year is
          what the eye reads first, and the cards below give it its exact numbers. */}
      <MonthlyBars
        title={`Andamento economico ${year}`}
        months={pension_fund.months}
        series={purchasesOn ? ['paid', 'costs'] : ['paid']}
        shares="trend_shares"
      />
      <MonthlyBars
        title={`Proiezione economica ${year}`}
        months={pension_fund.months}
        series={purchasesOn ? ['paid', 'unpaid', 'drafts', 'costs'] : ['paid', 'unpaid', 'drafts']}
        shares="projection_shares"
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <BigNumber label="Ricavi incassati" value={money(pension_fund.paid)} tone="accent" />
        <BigNumber
          label="Ricavi proiettati"
          value={money(pension_fund.projected)}
          hint={`Incassato ${money(pension_fund.paid)} · da incassare ${money(pension_fund.unpaid)} · bozze e proforma ${money(pension_fund.drafts)}`}
        />
        {purchasesOn && (
          <>
            <BigNumber label="Costi passivi" value={money(pension_fund.costs)} />
            <BigNumber
              label="Totale lordo effettivo"
              value={money(pension_fund.actual_gross)}
              hint="incassato meno costi"
            />
          </>
        )}
        {!taxesOn ? null : tax?.company ? (
          <>
            {/* An srl owes IRES and IRAP, on what accrued in the year (REB-1073): the same
                estimate `get_fiscal_estimate` answers, one card per tax and the total the
                server adds. No projection and no net: the projection is a «what if» on
                revenue alone and the srl's estimate needs the year's costs. */}
            <BigNumber
              label="IRES stimata"
              value={estimated(tax.company.ires.amount)}
              hint={tax.company.ires.loss ? 'anno in perdita' : `aliquota ${formatPercent(tax.company.ires.rate)}`}
            />
            <BigNumber
              label="IRAP stimata"
              value={estimated(tax.company.irap.amount)}
              hint={tax.company.irap.missing ?? irapHint(tax.company.irap)}
            />
            <BigNumber
              label="Totale da saldare"
              value={due(tax)}
              hint={tax.basis ? `${tax.basis.label}, su base competenza` : 'su base competenza'}
            />
          </>
        ) : tax?.person ? (
          <>
            {/* An ordinary person owes IRPEF with its surcharges and INPS, on what was collected
                less what was paid in the year (REB-1072): the same estimate `get_fiscal_estimate`
                answers, one card per tax and the total the server adds. No projection and no
                net: those start from revenue alone, and this estimate needs the year's costs and
                withholdings. */}
            <BigNumber
              label="IRPEF stimata"
              value={estimated(tax.person.irpef.amount)}
              hint={
                tax.person.irpef.with_surcharges
                  ? `con le addizionali ${money(tax.person.irpef.with_surcharges)}, al netto delle ritenute`
                  : 'senza addizionali: i dettagli sono in Tasse'
              }
            />
            <BigNumber
              label="Contributi INPS stimati"
              value={estimated(tax.person.inps.amount)}
              hint={
                tax.person.inps.amount === null ? 'manca qualcosa: i dettagli sono in Tasse' : undefined
              }
            />
            <BigNumber
              label="Totale da saldare"
              value={estimated(tax.due_total ?? null)}
              hint={tax.basis ? `${tax.basis.label}, su base incasso` : 'su base incasso'}
            />
          </>
        ) : tax ? (
          <>
            <BigNumber
              label="Imponibile forfettario stimato"
              value={money(tax.taxable_amount ?? '0.00')}
              hint={
                projected_tax?.taxable_amount
                  ? `con proiezione ${money(projected_tax.taxable_amount)}`
                  : undefined
              }
            />
            <BigNumber
              label="Totale da saldare"
              value={due(tax)}
              hint={`imposta sostitutiva ${estimated(tax.substitute_tax)} · INPS ${estimated(tax.contributions)}`}
            />
            {/* The revenue the estimate was actually computed on, from the estimate
                itself: under the accrual reading `cassa.proiettato` can be a different
                figure (a document whose period and payment fall in different years). */}
            <BigNumber
              label="Totale da saldare con proiezione"
              value={due(projected_tax)}
              hint={
                projected_tax
                  ? `su ricavi proiettati ${money(projected_tax.revenue)}, su base incasso`
                  : undefined
              }
            />
            {/* The two nets are on the money whatever the switch says (the forfettario is
                taxed on what was collected in the year; DECISIONS.md, 2026-09-10), so the
                hints name the base instead of pointing at a lordo card that, under the
                accrual reading, may be a different figure. */}
            <BigNumber
              label="Totale netto ricavi"
              value={actual_net ? money(actual_net) : '—'}
              hint="incassato meno costi e tasse stimate, su base incasso"
            />
            <BigNumber
              label="Totale netto ricavi con proiezione"
              value={projected_net ? money(projected_net) : '—'}
              hint="proiettato meno costi e tasse stimate, su base incasso"
            />
          </>
        ) : perLegalEntity ? (
          <div className="border bg-card p-4 sm:col-span-2">
            <p className="text-sm text-muted-foreground">Stima fiscale</p>
            {/* Two regimes' taxes do not add (spec §1.9): the server sends no estimate in
                «tutte» and the cards below carry one per azienda instead. */}
            <p className="mt-1 text-sm">
              Con più aziende la stima è una per azienda, ciascuna sui propri ricavi e sul
              proprio regime: le trovi qui sotto, per chi amministra lo spazio e per le
              aziende con un profilo fiscale configurato.
            </p>
          </div>
        ) : (
          <div className="border bg-card p-4 sm:col-span-2">
            <p className="text-sm text-muted-foreground">Stima fiscale</p>
            {/* No link out any more: the card below says which of the two is missing, in
                the server's own words, and offers the settings screen when that is the
                answer. */}
            <p className="mt-1 text-sm">
              Non disponibile qui: serve un profilo fiscale del regime forfettario, ordinario o di una
              società di capitali e un account amministratore. Gli altri regimi non hanno una
              stima in PigroCRM.
            </p>
          </div>
        )}
      </div>

      {/* The room left under the forfettario's ceilings and the «ci sta?» simulator
          (REB-755), right under the figures they bound: the cards above say how much was
          collected, this says how much more can be before the regime ends. It draws
          nothing for an azienda those ceilings do not apply to. */}
      {!taxesOn ? null : perLegalEntity ? (
        legal_entities.map((legal_entity) => (
          <CeilingPanel
            key={legal_entity.id}
            year={year}
            legalEntityId={legal_entity.id}
            title={legal_entity.name}
          />
        ))
      ) : (
        <CeilingPanel year={year} />
      )}

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
          La quota di ciascun cliente sui ricavi fatturati nell’anno. Non è
          l’esposizione residua di «Da incassare» nella scheda scadenze, che risponde a
          una domanda diversa (chi deve di più oggi).
        </p>
        {customer_concentration.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna fattura emessa nell’anno.</p>
        ) : perLegalEntity ? (
          // One table per legal entity, each share of that legal entity's own revenue: a
          // customer who is a third of one legal entity reads as a third, never as a tenth
          // of the whole (spec §1.9). An legal entity deactivated meanwhile still owns its
          // year's invoices and is named as such.
          byLegalEntity(customer_concentration).map((group) => (
            <div key={group.legalEntityId} className="space-y-1">
              <h3 className="text-sm font-medium text-muted-foreground">
                {byId(group.legalEntityId)?.name ?? 'Azienda non più attiva'}
              </h3>
              <ConcentrationTable
                rows={group.rows}
                label={`Concentrazione dei ricavi per cliente, ${
                  byId(group.legalEntityId)?.name ?? 'Azienda non più attiva'
                }`}
              />
            </div>
          ))
        ) : (
          <ConcentrationTable rows={customer_concentration} />
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
      {!taxesOn ? null : perLegalEntity ? (
        legal_entities.map((legal_entity) => (
          <FiscalPanel
            key={legal_entity.id}
            year={year}
            legalEntityId={legal_entity.id}
            title={legal_entity.name}
            quiet
          />
        ))
      ) : (
        <FiscalPanel year={year} />
      )}

      {modules.on('cash') && (
        <p className="text-sm text-muted-foreground">
          Questa vista conta quando il lavoro è maturato; «Cassa» conta quando i soldi entrano ed escono, e lo
          stesso mese può leggersi in modo diverso nelle due.{' '}
          <Link to="/app/cash" className="underline underline-offset-4">
            Apri Cassa
          </Link>
        </p>
      )}
    </div>
  )
}
