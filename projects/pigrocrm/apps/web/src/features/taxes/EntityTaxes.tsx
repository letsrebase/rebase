import { Link } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { TabsContent, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useFiscalEstimate } from '@/features/dashboard/queries'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { useModules } from '@/lib/modules'
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
import { useTaxCalendar } from './queries'
import { TABS, type TaxesTab } from './search'

/** The table's width from `md`, the inline panel's from `lg`: below them rows are cards and the
 *  panel is a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

type Open = { kind: 'tax'; tax: Tax } | { kind: 'date'; date: string } | null

function Failure({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="space-y-3">
      <QueryErrorBanner error={error} />
      <Button variant="outline" size="sm" onClick={onRetry}>
        Riprova
      </Button>
    </div>
  )
}

/** The panel beside a table from `lg`, a full-screen sheet below it. */
function Detail({
  inline,
  label,
  onClose,
  children,
}: {
  inline: boolean
  label: string
  onClose: () => void
  children: ReactNode
}) {
  if (inline) {
    return (
      <aside aria-label={label} className="flex w-82.5 shrink-0 flex-col self-start border bg-card">
        <div className="flex justify-end border-b px-2 py-1">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Chiudi
          </Button>
        </div>
        {children}
      </aside>
    )
  }
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        closeLabel="Chiudi"
        className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none"
      >
        <SheetTitle className="sr-only">{label}</SheetTitle>
        <SheetDescription className="sr-only">{label}</SheetDescription>
        <div className="flex min-h-0 flex-1 flex-col pt-12">{children}</div>
      </SheetContent>
    </Sheet>
  )
}

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
    const days = groupByDueDate(calendar.data?.dates ?? [])
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
    ) : calendar.isPending ? (
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
          {missingParameter ? (
            <p className="text-sm text-muted-foreground">{missingParametersSentence(data.missing_parameters)}</p>
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
          <DatePanel day={openDay} />
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
            {missingParameter ? (
              <p className="text-sm text-muted-foreground">{missingParametersSentence(data.missing_parameters)}</p>
            ) : null}
            {pack ? <p className="text-xs text-muted-foreground">{pack}.</p> : null}
          </section>
        )}
        {days.length > 0 || calendar.isPending || calendar.isError ? (
          <section aria-label="Scadenze" className="space-y-3">
            <h3 className="text-base font-semibold">Scadenze</h3>
            {dateRows}
          </section>
        ) : null}
        {openDay ? (
          <Detail inline={false} label="Scadenza aperta" onClose={() => setOpen(null)}>
            <DatePanel day={openDay} />
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
