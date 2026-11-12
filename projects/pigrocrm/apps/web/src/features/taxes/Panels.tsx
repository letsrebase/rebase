import { Link } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { NOT_COMPUTABLE, formatPercent } from '@/features/analytics/format'
import { money } from '@/features/dashboard/format'
import { useSettleDueItem } from '@/features/cash/schedule/queries'
import { moneyIn } from '@/features/cash/money'
import { sourceLabel } from '@/features/cash/schedule/labels'
import { formatIsoDateItalian, toIsoDate } from '@/lib/dates'
import { useLegalEntity } from '@/lib/legalEntity'
import { dateLabel, taxOfDate, type DueDay } from './dates'
import { AdvanceMethodDialog } from './AdvanceMethodDialog'
import { DeclaredFigureDialog } from './DeclaredFigureDialog'
import { F24Section } from './F24Section'
import { IrapRegions } from './IrapRegions'
import { InstalmentPlanDialog } from './InstalmentPlanDialog'
import { TaxLossSection } from './TaxLosses'
import {
  CARRY_LABEL,
  CARRY_TONE,
  TAX_ITEM_STATE_LABEL,
  TAX_ITEM_STATE_TONE,
  groupedItemIds,
  overdueSince,
  partlyPaid,
  taxItemState,
} from './items'
import {
  ADVANCE_METHOD_LABELS,
  INCOME_TAXES,
  missingParametersSentence,
  NOT_ONE_YET,
  STAGES,
  STAGE_LABELS,
  TAX_LABELS,
  packLine,
  type FiscalEstimate,
  type OrdinaryLine,
  type Tax,
  type TaxReadingLine,
} from './labels'
import { failureText, useConfirmTaxCarry, useRefreshTaxes, useTaxPlans, type TaxDueItem } from './queries'
import { readingCell } from './readings'

function Fact({ label, children, strong = false }: { label: string; children: ReactNode; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b py-2 text-sm last:border-0">
      <dt className={cn(strong && 'font-semibold')}>{label}</dt>
      <dd className={cn('text-right tabular-nums', strong && 'font-semibold')}>{children}</dd>
    </div>
  )
}

/** A figure of a declared return, or the dash that says it was not given (never zero). */
function given(figure: string | null | undefined): string {
  return figure == null ? NOT_ONE_YET : money(figure)
}

/** «31/12»: the day and month a fiscal year ends, as the legal entity stores them. */
function dayMonth(day: number, month: number): string {
  return `${String(day).padStart(2, '0')}/${String(month).padStart(2, '0')}`
}

/** A line of an estimate: the figure, or «non calcolabile» for one the server could not give
 *  (never `0,00 €`: a zero reads as «you owe nothing»). */
function figure(value: string | null | undefined): string {
  return value == null ? NOT_COMPUTABLE : money(value)
}

function Lines({ lines }: { lines: readonly OrdinaryLine[] }) {
  return (
    <>
      {lines.map((line) => (
        <Fact key={line.code} label={line.label}>
          {figure(line.amount)}
        </Fact>
      ))}
    </>
  )
}

/** What an ordinary person's estimate says it assumes, was read from, and lacks: the server's
 *  sentences, drawn as they come. */
function Notes({
  missing,
  assumptions,
  sources,
}: {
  missing: readonly string[]
  assumptions: readonly string[]
  sources: readonly string[]
}) {
  return (
    <>
      {missing.map((sentence) => (
        <p key={sentence} className="text-sm">
          {sentence}
        </p>
      ))}
      <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
        {assumptions.map((assumption) => (
          <li key={assumption}>{assumption}</li>
        ))}
      </ul>
      {sources.length === 0 ? null : (
        <div className="space-y-1 text-xs text-muted-foreground">
          <p className="font-medium">Fonti della legge</p>
          <ul className="list-disc space-y-1 pl-4">
            {sources.map((source) => (
              <li key={source}>{source}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

/**
 * What an ordinary person's IRPEF or INPS estimate was computed from (REB-1072): the year's
 * basis, the income from the collected revenue to the base of the IRPEF, the tax's own lines
 * with the surcharges and the withholdings, and what the figure assumes. Every number is the
 * server's; nothing is added here.
 */
function PersonComputedFrom({ line, estimate }: { line: TaxReadingLine; estimate: FiscalEstimate }) {
  const person = estimate.person
  if (!person || (line.tax !== 'irpef' && line.tax !== 'inps')) return null
  const tax = line.tax === 'irpef' ? person.irpef : person.inps
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">Come è calcolata la stima</h3>
      {estimate.basis ? <p className="text-xs text-muted-foreground">{estimate.basis.label}.</p> : null}
      {line.tax === 'irpef' ? (
        <dl>
          <Lines lines={person.income.lines} />
          <Fact label={person.income.loss ? 'Reddito (in perdita)' : 'Reddito su cui si calcola l’IRPEF'} strong>
            {figure(person.income.taxable_income)}
          </Fact>
          <Lines lines={tax.parts.filter((part) => !part.code.endsWith('_surcharge'))} />
          <Fact label="IRPEF stimata" strong>
            {figure(tax.amount)}
          </Fact>
          {tax.credit != null && tax.credit !== '0.00' ? (
            <Fact label="Ritenute oltre l’IRPEF (credito)">{money(tax.credit)}</Fact>
          ) : null}
          <Lines lines={tax.parts.filter((part) => part.code.endsWith('_surcharge'))} />
          <Fact label="IRPEF con le addizionali" strong>
            {figure(tax.with_surcharges)}
          </Fact>
        </dl>
      ) : (
        <dl>
          <Lines lines={tax.lines} />
          <Fact label="Base dei contributi">{figure(tax.base)}</Fact>
          <Fact label="Aliquota">{tax.rate == null ? NOT_COMPUTABLE : formatPercent(tax.rate)}</Fact>
          <Lines lines={tax.parts} />
          <Fact label="Contributi stimati" strong>
            {figure(tax.amount)}
          </Fact>
        </dl>
      )}
      <Notes
        missing={tax.missing}
        assumptions={line.tax === 'irpef' ? [...person.income.assumptions, ...tax.assumptions] : tax.assumptions}
        sources={line.tax === 'irpef' ? [...new Set([...person.income.sources, ...tax.sources])] : tax.sources}
      />
    </section>
  )
}

/**
 * What an srl's IRES or IRAP estimate was computed from (REB-1073): the year's basis, each line
 * down to the base with its sign, the rate and the tax, and what the figure assumes. Every number
 * is the server's; nothing is added here.
 */
function ComputedFrom({ line, estimate }: { line: TaxReadingLine; estimate: FiscalEstimate }) {
  const tax = line.tax === 'ires' ? estimate.company?.ires : line.tax === 'irap' ? estimate.company?.irap : undefined
  const basis = estimate.basis
  // The IRAP of an srl with people in several regions has no one rate: the table says each (REB-1092).
  const split = (tax?.regions?.length ?? 0) > 0
  // An ordinary person's IRPEF and INPS are drawn by `PersonComputedFrom`, which says its own basis.
  if (estimate.person && (line.tax === 'irpef' || line.tax === 'inps')) return null
  if (!tax && !basis) return null
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Come è calcolata la stima</h3>
      {basis ? (
        <div className="space-y-1 text-xs text-muted-foreground">
          <p>{basis.label}.</p>
          <p>
            Dal {formatIsoDateItalian(basis.period_from)} al {formatIsoDateItalian(basis.period_to)}: {basis.wording}
          </p>
        </div>
      ) : null}
      {tax ? (
        <dl>
          {tax.lines.map((step) => (
            <Fact key={step.code} label={step.label}>
              {money(step.amount)}
            </Fact>
          ))}
          <Fact label={tax.loss ? 'Base (in perdita)' : 'Base'} strong>
            {given(tax.base)}
          </Fact>
          {split ? null : <Fact label="Aliquota">{tax.rate == null ? NOT_ONE_YET : formatPercent(tax.rate)}</Fact>}
          <Fact label="Imposta stimata" strong>
            {given(tax.amount)}
          </Fact>
        </dl>
      ) : null}
      {split ? <IrapRegions regions={tax?.regions ?? []} /> : null}
      {tax?.missing ? <p className="text-sm">{tax.missing}</p> : null}
      {tax ? (
        <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
          {tax.assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ul>
      ) : null}
      {basis && basis.assumptions.length > 0 ? (
        <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
          {basis.assumptions.map((assumption) => (
            <li key={assumption.code}>{assumption.reason}</li>
          ))}
        </ul>
      ) : null}
      {basis && basis.left_out.length > 0 ? (
        <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
          {basis.left_out.map((left) => (
            <li key={left.code}>{left.reason}</li>
          ))}
        </ul>
      ) : null}
      {estimate.missing_parameters && estimate.missing_parameters.length > 0 ? (
        <p className="text-sm">
          {missingParametersSentence(estimate.missing_parameters)}{' '}
          <Link to="/app/settings/legal-entities" className="underline">
            Apri i parametri fiscali
          </Link>
        </p>
      ) : null}
    </section>
  )
}

/**
 * A tax's detail, read-only: its three readings and which one counts, the figures of the typed
 * return beside them (tax due, balance, advances, who typed them as the system records it: by
 * hand, and when), the pack and its law's year, and the entity's fiscal year end. «Digita il
 * dichiarato» (or «Correggi il dichiarato» once there is one) opens the dialog that types the
 * three figures of the return; the panel reads the declared figure as the one that counts as
 * soon as the server has it. Choosing the advance method is its own card.
 */
export function TaxPanel({
  line,
  year,
  estimate,
}: {
  line: TaxReadingLine
  year: number
  estimate: FiscalEstimate
}) {
  const { byId } = useLegalEntity()
  // The entity the figures were computed for, whoever the sidebar has selected.
  const entity = byId(estimate.legal_entity_id)
  const figure = (estimate.declared ?? []).find((candidate) => candidate.tax === line.tax)
  const pack = packLine(estimate)
  const [dialog, setDialog] = useState<'method' | 'instalments' | 'declared' | null>(null)
  const plans = useTaxPlans(estimate.legal_entity_id)
  const income = INCOME_TAXES.includes(line.tax)
  const plan = plans.data?.instalment_plans.find(
    (candidate) => candidate.tax === line.tax && candidate.tax_year === year,
  )
  return (
    <div className="space-y-5 overflow-y-auto p-4">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">
          {TAX_LABELS[line.tax]} {year}
        </h2>
        <p className="text-sm text-muted-foreground">
          {line.counts === null ? 'Nessuna cifra ancora.' : `Vale: ${STAGE_LABELS[line.counts]}.`}
        </p>
      </div>
      <dl>
        {STAGES.map((stage) => (
          <Fact key={stage} label={STAGE_LABELS[stage]} strong={line.counts === stage}>
            {readingCell(line, stage)}
          </Fact>
        ))}
      </dl>
      {figure ? (
        <section className="space-y-1">
          <h3 className="text-sm font-semibold">Il dichiarato</h3>
          <dl>
            <Fact label="Imposta dovuta">{given(figure.tax_due)}</Fact>
            <Fact label="Saldo">{given(figure.balance)}</Fact>
            <Fact label="Acconti">{given(figure.advances)}</Fact>
          </dl>
          <p className="text-xs text-muted-foreground">
            Digitato a mano il {formatIsoDateItalian(toIsoDate(new Date(figure.updated_at)))}. Un
            dichiarato letto da un documento si accetta in Proposte.
          </p>
        </section>
      ) : null}
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => setDialog('declared')}>
          {figure ? 'Correggi il dichiarato' : 'Digita il dichiarato'}
        </Button>
      </div>
      {(line.tax === 'ires' && estimate.company) || (line.tax === 'irpef' && estimate.person) ? (
        <TaxLossSection estimate={estimate} tax={line.tax as 'ires' | 'irpef'} year={year} />
      ) : null}
      <ComputedFrom line={line} estimate={estimate} />
      <PersonComputedFrom line={line} estimate={estimate} />
      {income ? (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Acconti e rate</h3>
          <dl>
            <Fact label="Metodo dell’acconto">{ADVANCE_METHOD_LABELS[estimate.advance_method].label}</Fact>
            <Fact label="Rateazione di giugno">
              {plan ? `${plan.instalments} rate` : 'Nessuna'}
            </Fact>
          </dl>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setDialog('instalments')}>
              Rateizza giugno
            </Button>
            <Button variant="outline" size="sm" onClick={() => setDialog('method')}>
              Cambia il metodo dell’acconto
            </Button>
          </div>
        </section>
      ) : null}
      {dialog === 'declared' ? (
        <DeclaredFigureDialog
          legalEntityId={estimate.legal_entity_id}
          year={year}
          tax={line.tax as Tax}
          current={figure}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === 'method' ? (
        <AdvanceMethodDialog
          legalEntityId={estimate.legal_entity_id}
          year={year}
          current={estimate.advance_method}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === 'instalments' ? (
        <InstalmentPlanDialog
          legalEntityId={estimate.legal_entity_id}
          year={year}
          tax={line.tax as Tax}
          current={plan?.instalments}
          onClose={() => setDialog(null)}
        />
      ) : null}
      <section className="space-y-1 text-xs text-muted-foreground">
        {pack ? <p>{pack}.</p> : null}
        {entity ? (
          <p>Fine dell’esercizio: {dayMonth(entity.fiscal_year_end_day, entity.fiscal_year_end_month)}.</p>
        ) : null}
      </section>
    </div>
  )
}

/** One due item in a date's panel: its words in full, its amount and state, what is left of a
 *  part payment, the mark and the button of a carried difference, and «Segna come pagato». */
function ItemBlock({ item, today, grouped }: { item: TaxDueItem; today: string; grouped: boolean }) {
  const state = taxItemState(item, today)
  const confirm = useConfirmTaxCarry()
  const settle = useSettleDueItem()
  const refresh = useRefreshTaxes()
  const since = state === 'overdue' ? overdueSince(item, today) : null
  // An item in an F24 is paid with the group's movement, not on its own.
  const open = (item.state === 'expected' || item.state === 'forecast') && !grouped
  return (
    <li className="space-y-2 text-sm">
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-medium">{sourceLabel(item.source_kind)}</p>
        <p className="tabular-nums">{moneyIn(item.amount, item.currency)}</p>
      </div>
      {item.description ? <p className="text-muted-foreground">{item.description}</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={TAX_ITEM_STATE_TONE[state]}>{TAX_ITEM_STATE_LABEL[state]}</StatusPill>
        {since ? <span className="text-xs text-muted-foreground">{since}</span> : null}
        {item.carry_to_confirm ? <StatusPill tone={CARRY_TONE}>{CARRY_LABEL}</StatusPill> : null}
      </div>
      {partlyPaid(item) ? (
        <p className="text-xs text-muted-foreground">
          Pagati {moneyIn(item.settled, item.currency)}, restano {moneyIn(item.remaining, item.currency)}.
        </p>
      ) : null}
      {item.due_date_estimated ? (
        <p className="text-xs text-muted-foreground">La data segue un’altra scadenza: è una stima.</p>
      ) : null}
      {confirm.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {failureText(confirm.error)}
        </p>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        {item.carry_to_confirm ? (
          <Button
            variant="outline"
            size="sm"
            disabled={confirm.isPending}
            onClick={() => confirm.mutate(item.id)}
          >
            Conferma la differenza
          </Button>
        ) : null}
        {open ? (
          <Button size="sm" disabled={settle.isPending} onClick={() => settle.mutate({ item, today }, { onSuccess: refresh })}>
            Segna come pagato
          </Button>
        ) : null}
      </div>
    </li>
  )
}

/**
 * A date's detail: the due items it holds (a balance, an advance, an instalment, the chamber
 * fee), each with what it says, owes and stands at, then the lines the pack gives it, each with
 * the source the pack names it from and the nominal day when a weekend or a holiday moved it
 * forward.
 */
export function DatePanel({ day, today }: { day: DueDay; today: string }) {
  const grouped = groupedItemIds(day.groups)
  return (
    <div className="space-y-5 overflow-y-auto p-4">
      <h2 className="text-lg font-semibold">{formatIsoDateItalian(day.date)}</h2>
      <F24Section day={day} today={today} />
      {day.items.length > 0 ? (
        <ul className="space-y-4 border-b pb-4">
          {day.items.map((item) => (
            <ItemBlock
              key={item.id}
              item={item}
              today={today}
              grouped={grouped.has(item.id)}
            />
          ))}
        </ul>
      ) : null}
      <ul className="space-y-4">
        {day.entries.map((entry) => (
          <li key={`${entry.tax}-${entry.kind}`} className="space-y-1 text-sm">
            <p className="font-medium">{dateLabel(entry)}</p>
            <p className="text-muted-foreground">{taxOfDate(entry)}</p>
            {entry.nominal_date === entry.due_date ? null : (
              <p className="text-xs text-muted-foreground">
                Scadenza del {formatIsoDateItalian(entry.nominal_date)}, spostata al primo giorno
                lavorativo.
              </p>
            )}
            <p className="text-xs text-muted-foreground">Fonte: {entry.source}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}
