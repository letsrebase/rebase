import type { ReactNode } from 'react'
import { cn } from '@rebase/ui/cn'
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
