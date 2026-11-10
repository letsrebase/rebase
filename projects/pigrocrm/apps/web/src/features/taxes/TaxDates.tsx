import { Link } from '@tanstack/react-router'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { useLegalEntity } from '@/lib/legalEntity'
import { useModules } from '@/lib/modules'
import { NOT_COMPUTABLE } from '@/features/analytics/format'
import { dateLabel, distanceLabel } from './dates'
import { LOADING_SENTENCE, NOTHING_DUE } from './labels'
import { useNextTaxDates } from './queries'

/** Where the block sits (brief, «The Home's tax dates»): `block` is the solo Home's own section
 *  after «Quando arrivano i soldi» and the phone's card; `band` is the company Home's, under
 *  «Da fare» and above the tabs, so every tab shows it. */
export type TaxDatesVariant = 'block' | 'band'

const HEADINGS: Record<TaxDatesVariant, string> = {
  block: 'Prossime tasse',
  band: 'Prossime scadenze fiscali',
}

/**
 * The next two tax dates of one legal entity, with the amount of each: «30/11/2026 · tra 20
 * giorni», what falls due under it and the amount at the right. The dates are the pack's, once
 * per calendar «salvo proroghe» on the page; the amount is a date's net as the server gives it,
 * and no tax due item carries one yet, so it reads «non calcolabile», never `0,00 €` (brief,
 * «Amount not known»).
 *
 * It draws nothing when the legal entity has no fiscal profile (404: Settings says so) and
 * nothing when its pack carries no dates (the page says why; the Home is not where a gap in the
 * law is explained). A failed read is said, never an empty block.
 */
export function TaxDatesBlock({
  legalEntityId,
  name,
  variant,
}: {
  /** One legal entity's, by id; omitted, the sidebar's selection. */
  legalEntityId?: string
  /** The legal entity's name, drawn only when several are listed. */
  name?: string
  variant: TaxDatesVariant
}) {
  const next = useNextTaxDates(legalEntityId)
  const failure = next.error
  if (failure !== null && [403, 404].includes(toProblem(failure).status)) return null
  if (!next.waiting && failure === null && !next.hasCalendar) return null
  const heading = name ? `${HEADINGS[variant]} · ${name}` : HEADINGS[variant]
  return (
    <section
      aria-label={heading}
      className={cn('space-y-3', variant === 'band' && 'px-8 pb-6')}
    >
      <h2 className="text-base font-semibold">{heading}</h2>
      {failure !== null ? (
        <div className="space-y-3">
          <QueryErrorBanner error={failure} />
          <Button variant="outline" size="sm" onClick={next.refetch}>
            Riprova
          </Button>
        </div>
      ) : next.waiting ? (
        <LoadingState label={LOADING_SENTENCE} />
      ) : next.days.length === 0 ? (
        <p className="text-sm text-muted-foreground">{NOTHING_DUE}</p>
      ) : (
        <div className="space-y-2">
          <ul className="divide-y border bg-card">
            {next.days.map((day) => (
              <li key={day.date} className="flex items-baseline justify-between gap-4 px-4 py-3">
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[13px] font-medium">
                    {formatIsoDateItalian(day.date)} · {distanceLabel(next.today, day.date)}
                  </p>
                  {day.entries.map((entry) => (
                    <p
                      key={`${entry.tax}-${entry.kind}`}
                      className="text-xs text-muted-foreground"
                    >
                      {dateLabel(entry)}
                    </p>
                  ))}
                </div>
                <span className="shrink-0 text-[13px] text-muted-foreground tabular-nums">
                  {NOT_COMPUTABLE}
                </span>
              </li>
            ))}
          </ul>
          <div className="text-right">
            <Link
              to="/app/taxes"
              search={{ tab: 'taxes' }}
              className="text-sm underline underline-offset-4"
            >
              Apri Tasse
            </Link>
          </div>
        </div>
      )}
    </section>
  )
}

/**
 * The Home's tax dates for whoever may read them: one block per legal entity in «Tutte le
 * aziende» (each row names its entity and none is merged into another), one block with no name
 * otherwise. Not drawn, and nothing requested, when `taxes` is off or the role is not an
 * administrator: the Home is every role's start page, and «Tasse» explains (brief, «Not an
 * administrator»).
 */
export function HomeTaxDates({ variant }: { variant: TaxDatesVariant }) {
  const modules = useModules()
  const allowed = useCan('get_tax_calendar')
  const { selected, legal_entities, several } = useLegalEntity()
  if (!modules.on('taxes') || !allowed) return null
  if (several && selected === null) {
    return (
      <>
        {legal_entities.map((legalEntity) => (
          <TaxDatesBlock
            key={legalEntity.id}
            legalEntityId={legalEntity.id}
            name={legalEntity.name}
            variant={variant}
          />
        ))}
      </>
    )
  }
  return <TaxDatesBlock variant={variant} />
}
