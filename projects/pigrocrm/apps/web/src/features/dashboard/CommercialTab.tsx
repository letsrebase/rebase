import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Skeleton } from '@rebase/ui/skeleton'
import { BarRows, BigNumber, type BarRow } from './charts'
import { money, percent } from './format'
import { Freshness } from './Freshness'
import type { Periodo } from './periodo'
import { useCommercialDashboard, type PipelineStageSummary } from './queries'

/** Ink for won and the `destructive` badge's own tint for lost -- two outcomes, told the
 *  way the rest of the app already tells them, and no sixth colour. The open stages keep
 *  the chart hues, so the sequence still reads as a sequence. */
const CLOSED_FILL: Partial<Record<PipelineStageSummary['stage_tipo'], string>> = {
  won: 'var(--foreground)',
  lost: 'color-mix(in oklab, var(--destructive) 10%, transparent)',
}

function isOpen(row: PipelineStageSummary) {
  return row.stage_tipo === 'open'
}

/**
 * Since 2026-09-08 the commercial tab is the first row and the pipeline: what closed in
 * the period (won, lost, conversion, value) and where the deals are by stage. The detail
 * table, the pending offers, the 30-day forecast and the signals left the page; the API
 * still returns them for the agent.
 *
 * Since 2026-09-09 the card is the *whole* pipeline: «Vinto» and «Perso» are drawn too,
 * after the open stages and behind a hairline. A card that stopped at the last open stage
 * never said where the work ended up, which is the one question a pipeline exists to
 * answer -- and those two rows count what sits in the stage today, not what closed in the
 * period, which is what the four figures above the card already say.
 */
export function CommercialTab({ periodo }: { periodo: Periodo }) {
  const query = useCommercialDashboard(periodo)

  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  const data = query.data
  // Grouped by `stage_tipo` and never by `stage_nome`: the label is a string the user is
  // free to rename in Impostazioni, and `tipo` is the attribute the rest of the product
  // already reads a stage's meaning from. Within each group the server's `posizione`
  // order is kept as it arrived.
  const open = data.pipeline.filter(isOpen)
  const closed = data.pipeline.filter((row) => !isOpen(row))
  // The widest stage of *all* of them, closed included: two bars side by side that were
  // scaled against different maxima is a chart that lies about the pair it drew.
  const widest = data.pipeline.reduce((best, row) => (row.numero > best ? row.numero : best), 0)
  const bar = (row: PipelineStageSummary, index: number): BarRow => ({
    label: row.stage_nome,
    value: `${row.numero}`,
    ratio: widest === 0 ? 0 : row.numero / widest,
    tone: index + 1,
    color: CLOSED_FILL[row.stage_tipo],
    // The hairline belongs to the first closed row, so a pipeline with no closed stage
    // (or no open one) never draws a rule with nothing on one side of it.
    separator: open.length > 0 && index === open.length,
  })
  const bars: BarRow[] = [...open, ...closed].map(bar)

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <Freshness calcolatoAlle={data.calcolato_alle} onRefresh={() => void query.refetch()} />
      </div>

      {/* The pipeline first, then the closures (2026-09-09): what is open is the picture,
          what closed is the caption under it. */}
      <BarRows caption="Pipeline per stato" rows={bars} />

      {/* The closed rows sit four inches above «Deal vinti nel periodo» (the card comes
          before the figures since 2026-09-09), and the two answer different questions:
          one is a place, the other a period. The MCP briefing says so to the agent
          reading it; this is the same sentence for the person reading the screen. Only
          when there is a closed row to caveat. */}
      {closed.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Vinto e Perso contano i deal che stanno oggi in quello stato, non il periodo.
        </p>
      )}

      {/* Three across (design spec §4), not four: the KPI card is wider now that its
          value is 30px, and the fourth card of a four-up row was the one that wrapped
          first on a laptop anyway. */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <BigNumber label="Deal vinti nel periodo" value={`${data.chiusure.vinti}`} />
        <BigNumber label="Deal persi nel periodo" value={`${data.chiusure.persi}`} />
        <BigNumber
          label="Tasso di conversione"
          value={percent(data.chiusure.tasso_conversione)}
          hint="vinti su vinti + persi"
        />
        <BigNumber
          label="Valore vinto nel periodo"
          value={money(data.chiusure.valore_vinto)}
          hint="valore dichiarato dai deal, non fatturato"
        />
      </div>

      {data.chiusure_non_attribuibili > 0 && (
        <p className="text-xs text-muted-foreground">
          {data.chiusure_non_attribuibili} deal chiusi prima dell&apos;introduzione di questa
          misura <strong>non sono attribuibili</strong> a un periodo e non entrano nelle cifre
          sopra.
        </p>
      )}
    </div>
  )
}
