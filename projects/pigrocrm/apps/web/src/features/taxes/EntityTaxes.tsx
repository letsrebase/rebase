import { Link } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { cn } from '@rebase/ui/cn'
import { TabsContent, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useFiscalEstimate } from '@/features/dashboard/queries'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { useModules } from '@/lib/modules'
import { Detail } from './Detail'
import { Failure } from './Failure'
import { PlansTab } from './PlansTab'
import { groupByDueDate } from './dates'
import { DatesTable } from './DatesTable'
import {
  EMPTY_SENTENCE,
  FLAT_RATE_PACK,
  FOREIGN_PACK,
  FOREIGN_SENTENCE,
  FROZEN_SENTENCE,
  LOADING_SENTENCE,
  missingParametersSentence,
  NO_CALENDAR_SENTENCE,
  NO_PROFILE_SENTENCE,
  SALVO_PROROGHE,
  packLine,
  type Tax,
} from './labels'
import { DatePanel, TaxPanel } from './Panels'
import { ReadingsTable, TaxReadingLines } from './ReadingsTable'
import { useTaxCalendar, useTaxDueItems } from './queries'
import { TABS, type TaxesTab } from './search'

/** The table's width from `md`, the inline panel's from `lg`: below them rows are cards and the
 *  panel is a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

type Open = { kind: 'tax'; tax: Tax } | { kind: 'date'; date: string } | null

/**
 * One legal entity's taxes for `year`: the server's warning sentence first, then, with the
 * tabs «Imposte» and «Scadenze», each tax's three readings and the calendar's dates; on a
 * solo flat-rate space (brief, question 4) one light page with no tabs, «Le tue tasse» as a card
 * per tax and «Scadenze» in a compact table, each section only when it has rows.
 *
 * Every state the brief draws is here: no fiscal profile, a regime with no taxes, a frozen
 * year, a pack with no calendar, a figure «non calcolabile». Nothing on this page is computed in
 * the browser: the lines, the reading that counts, the totals and the dates are the API's.
 */
export function EntityTaxes({
  year,
  tab,
  legalEntityId,
  name,
}: {
  year: number
  tab: TaxesTab
  /** One legal entity's, by id; omitted, the sidebar's selection. */
  legalEntityId?: string
  /** The legal entity's name, drawn above the block when several are listed. */
  name?: string
}) {
  const allowed = useCan('get_fiscal_estimate')
  const modules = useModules()
  const wide = useMediaQuery(TABLE)
  const inline = useMediaQuery(INLINE_PANEL)
  const [open, setOpen] = useState<Open>(null)
  const estimate = useFiscalEstimate(year, legalEntityId, allowed)
  const calendar = useTaxCalendar(year, legalEntityId, allowed && estimate.isSuccess)
  const entity = estimate.data?.legal_entity_id ?? null
  const dueItems = useTaxDueItems(year, entity, allowed && estimate.isSuccess)

  const heading = name ? <h2 className="text-lg font-semibold">{name}</h2> : null
  let body: ReactNode
  if (estimate.isError) {
    body =
      toProblem(estimate.error).status === 404 ? (
        <p className="text-sm text-muted-foreground">
          {NO_PROFILE_SENTENCE} Vai in Impostazioni e{' '}
          <Link to="/app/settings/legal-entities" className="underline">
            imposta i parametri fiscali
          </Link>
          .
        </p>
      ) : (
        <Failure error={estimate.error} onRetry={() => void estimate.refetch()} />
      )
  } else if (estimate.isPending) {
    body = <LoadingState label={LOADING_SENTENCE} />
  } else if (estimate.data.pack_id === FOREIGN_PACK) {
    body = <p className="text-sm text-muted-foreground">{FOREIGN_SENTENCE}</p>
  } else {
    const data = estimate.data
    const light = modules.profile === 'solo' && data.pack_id === FLAT_RATE_PACK
    const days = groupByDueDate(calendar.data?.dates ?? [], dueItems.data ?? [])
    const today = toIsoDate(new Date())
    const readings = data.readings
    const lines = readings?.lines ?? []
    const chosen = light ? 'taxes' : tab

    const warning = (
      <p role="note" className="border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
        {data.warning}
      </p>
    )
    const frozen = data.frozen ? (
      <p className="text-sm text-muted-foreground">{FROZEN_SENTENCE}</p>
    ) : null

    const dateRows = calendar.isError ? (
      <Failure error={calendar.error} onRetry={() => void calendar.refetch()} />
    ) : dueItems.isError ? (
      <Failure error={dueItems.error} onRetry={() => void dueItems.refetch()} />
    ) : calendar.isPending || dueItems.isPending ? (
      <LoadingState label={LOADING_SENTENCE} />
    ) : days.length === 0 ? (
      light ? null : <p className="text-sm text-muted-foreground">{NO_CALENDAR_SENTENCE(year)}</p>
    ) : (
      <div className="space-y-3">
        <DatesTable
          days={days}
          today={today}
          selected={open?.kind === 'date' ? open.date : null}
          wide={wide}
          compact={light}
          onSelect={(date) => setOpen({ kind: 'date', date })}
        />
        <p className="text-xs text-muted-foreground">{SALVO_PROROGHE}</p>
      </div>
    )

    const missingParameter = lines.some((line) => line.estimable && line.estimate === null)
    // An srl's estimate says what it lacks in its own words (an address, not a parameter of the
    // profile) and so does an ordinary person's (an INPS fact, an address, a rate not yet
    // published, REB-1072); the flat-rate one has the one sentence about the settings.
    const missingNotes = data.company
      ? [data.company.ires.missing, data.company.irap.missing].filter(
          (note): note is string => typeof note === 'string',
        )
      : data.person
        ? [...data.person.inps.missing, ...data.person.irpef.missing]
        : missingParameter
          ? [missingParametersSentence(data.missing_parameters)]
          : []
    const pack = packLine(data)
    const openTax = open?.kind === 'tax' ? lines.find((line) => line.tax === open.tax) : undefined
    const openDay = open?.kind === 'date' ? days.find((day) => day.date === open.date) : undefined

    const taxes =
      lines.length === 0 ? (
        <p className="text-sm text-muted-foreground">{EMPTY_SENTENCE}</p>
      ) : (
        <div className="space-y-3">
          {readings ? (
            <ReadingsTable
              readings={readings}
              selected={open?.kind === 'tax' ? open.tax : null}
              wide={wide}
              onSelect={(tax) => setOpen({ kind: 'tax', tax })}
            />
          ) : null}
          {missingNotes.map((note) => (
            <p key={note} className="text-sm text-muted-foreground">
              {note}
            </p>
          ))}
          {data.missing_parameters && data.missing_parameters.length > 0 ? (
            <p className="text-sm">
              <Link to="/app/settings/legal-entities" className="underline">
                Apri i parametri fiscali
              </Link>
            </p>
          ) : null}
          {pack ? <p className="text-xs text-muted-foreground">{pack}.</p> : null}
        </div>
      )

    const panel =
      chosen === 'taxes' && openTax ? (
        <Detail inline={inline} label="Imposta aperta" onClose={() => setOpen(null)}>
          <TaxPanel line={openTax} year={year} estimate={data} />
        </Detail>
      ) : chosen === 'dates' && openDay ? (
        <Detail inline={inline} label="Scadenza aperta" onClose={() => setOpen(null)}>
          <DatePanel day={openDay} today={today} />
        </Detail>
      ) : null

    body = light ? (
      <div className="space-y-6">
        {warning}
        {frozen}
        {lines.length === 0 ? (
          <p className="text-sm text-muted-foreground">{EMPTY_SENTENCE}</p>
        ) : (
          <section aria-label="Le tue tasse" className="space-y-3">
            <h3 className="text-base font-semibold">Le tue tasse</h3>
            <ul className="grid gap-3 md:grid-cols-2">
              {lines.map((line) => (
                <li key={line.tax} className="space-y-2 border bg-card p-4">
                  <TaxReadingLines line={line} />
                </li>
              ))}
            </ul>
            {missingNotes.map((note) => (
              <p key={note} className="text-sm text-muted-foreground">
                {note}
              </p>
            ))}
            {pack ? <p className="text-xs text-muted-foreground">{pack}.</p> : null}
          </section>
        )}
        {days.length > 0 || calendar.isPending || calendar.isError || dueItems.isPending || dueItems.isError ? (
          <section aria-label="Scadenze" className="space-y-3">
            <h3 className="text-base font-semibold">Scadenze</h3>
            {dateRows}
          </section>
        ) : null}
        {openDay ? (
          <Detail inline={false} label="Scadenza aperta" onClose={() => setOpen(null)}>
            <DatePanel day={openDay} today={today} />
          </Detail>
        ) : null}
      </div>
    ) : (
      <div className="space-y-4">
        <TabsList variant="line">
          {TABS.map((candidate) => (
            <TabsTrigger key={candidate.id} value={candidate.id}>
              {candidate.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {warning}
        {frozen}
        <div className={cn('flex gap-6', !inline && 'items-start')}>
          <div className="min-w-0 flex-1">
            <TabsContent value="taxes">{taxes}</TabsContent>
            <TabsContent value="dates">{dateRows}</TabsContent>
            <TabsContent value="plans">
              <PlansTab legalEntityId={data.legal_entity_id} wide={wide} inline={inline} today={today} />
            </TabsContent>
          </div>
          {inline ? panel : null}
        </div>
        {inline ? null : panel}
      </div>
    )
  }

  return (
    <section aria-label={name ?? 'Tasse'} className="space-y-4">
      {heading}
      {body}
    </section>
  )
}
