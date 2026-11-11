import type { ReactNode } from 'react'
import { cn } from '@rebase/ui/cn'
import { NOT_COMPUTABLE, formatPercent } from '@/features/analytics/format'
import { money } from '@/features/dashboard/format'
import { formatIsoDateItalian, toIsoDate } from '@/lib/dates'
import { useLegalEntity } from '@/lib/legalEntity'
import { dateLabel, taxOfDate, type DueDay } from './dates'
import {
  NOT_ONE_YET,
  STAGES,
  STAGE_LABELS,
  TAX_LABELS,
  packLine,
  type FiscalEstimate,
  type OrdinaryLine,
  type TaxReadingLine,
} from './labels'
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
  if (!tax) return null
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Come è calcolata la stima</h3>
      {estimate.basis ? <p className="text-xs text-muted-foreground">{estimate.basis.label}.</p> : null}
      <dl>
        {tax.lines.map((step) => (
          <Fact key={step.code} label={step.label}>
            {money(step.amount)}
          </Fact>
        ))}
        <Fact label={tax.loss ? 'Base (in perdita)' : 'Base'} strong>
          {given(tax.base)}
        </Fact>
        <Fact label="Aliquota">{tax.rate == null ? NOT_ONE_YET : formatPercent(tax.rate)}</Fact>
        <Fact label="Imposta stimata" strong>
          {given(tax.amount)}
        </Fact>
      </dl>
      {tax.missing ? <p className="text-sm">{tax.missing}</p> : null}
      <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
        {tax.assumptions.map((assumption) => (
          <li key={assumption}>{assumption}</li>
        ))}
      </ul>
    </section>
  )
}

/**
 * A tax's detail, read-only: its three readings and which one counts, the figures of the typed
 * return beside them (tax due, balance, advances, who typed them as the system records it: by
 * hand, and when), the pack and its law's year, and the entity's fiscal year end. Typing a
 * declared figure and choosing the advance method are their own cards.
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
      <ComputedFrom line={line} estimate={estimate} />
      <PersonComputedFrom line={line} estimate={estimate} />
      <section className="space-y-1 text-xs text-muted-foreground">
        {pack ? <p>{pack}.</p> : null}
        {entity ? (
          <p>Fine dell’esercizio: {dayMonth(entity.fiscal_year_end_day, entity.fiscal_year_end_month)}.</p>
        ) : null}
      </section>
    </div>
  )
}

/**
 * A date's detail: the lines it holds, each with what it settles and the source the pack names
 * it from, and the nominal day when a weekend or a holiday moved it forward.
 */
export function DatePanel({ day }: { day: DueDay }) {
  return (
    <div className="space-y-5 overflow-y-auto p-4">
      <h2 className="text-lg font-semibold">{formatIsoDateItalian(day.date)}</h2>
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
