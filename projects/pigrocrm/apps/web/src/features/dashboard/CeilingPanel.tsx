import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { useCappedContracts } from '@/features/contracts/queries'
import { useLegalEntity } from '@/lib/legalEntity'
import { toProblem } from '@/lib/api'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { Meter } from './charts'
import { FILL_TONE, consequence, fill, magnitude } from './ceilingFormat'
import { money } from './format'
import { useCeilingHeadroom, type CeilingStatus } from './queries'
import { Simulator } from './CeilingSimulator'

function CeilingRow({ ceiling }: { ceiling: CeilingStatus }) {
  const crossed = ceiling.exceeded
  return (
    <div role="group" aria-label={ceiling.label} className="space-y-2 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">{ceiling.label}</p>
        {crossed ? (
          <StatusPill tone="danger">Superata</StatusPill>
        ) : ceiling.alert_level ? (
          <StatusPill tone="gold">{ceiling.alert_level}</StatusPill>
        ) : null}
      </div>
      <div className="flex items-center gap-3">
        <Meter
          ratio={ceiling.usage}
          tone={FILL_TONE}
          color={fill(crossed, ceiling.alert_level)}
        />
        <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
          {money(ceiling.threshold)}
        </span>
      </div>
      <p className="text-sm">
        Incassati {money(ceiling.revenue)} su {money(ceiling.threshold)}:{' '}
        <strong className="font-semibold">
          {crossed
            ? `superata di ${magnitude(ceiling.remaining)}`
            : `restano ${magnitude(ceiling.remaining)}`}
        </strong>
        .
      </p>
      <p className="text-xs text-muted-foreground">
        {consequence(ceiling.consequence, crossed)}
      </p>
    </div>
  )
}

/**
 * Room left under each ceiling of the flat-rate regime, and the «ci sta?» simulator under it
 * (REB-755). What the server answers decides everything: a legal entity the thresholds do not
 * apply to (the ordinary regime, a company abroad) gets an empty list of ceilings. It then
 * draws only the simulator, under «Tetti dei contratti», when one of its active contracts
 * caps the customer's share (REB-761), and nothing at all when none does, not an empty box
 * that says it has nothing.
 *
 * Beside the estimate, one legal entity's and read from its own `year`, which is the year the
 * dashboard's period names. In «tutte» the tab draws one panel per legal entity, and one without
 * a profile (the estimate card says so once) or with neither ceilings nor capped contracts
 * draws nothing.
 */
export function CeilingPanel({
  year,
  legalEntityId,
  title,
}: {
  year: number
  /** One legal entity's ceilings, by id; omitted, the sidebar's selection. */
  legalEntityId?: string
  /** Shown beside the year in the heading: the legal entity's name in «tutte». */
  title?: string
}) {
  const headroom = useCeilingHeadroom(year, legalEntityId)
  // The legal entity the figures are of: the block's own, else the sidebar's. It keys the
  // simulator, so switching the sidebar starts the form over instead of asking the new
  // legal entity a question typed for the old one.
  const { selected } = useLegalEntity()
  const legal_entity = legalEntityId ?? selected
  const capped = useCappedContracts(legal_entity)

  if (headroom.isError) {
    // No profile (404): the estimate card above already says so, in the place that owns
    // the settings link. Saying it twice would be two prompts for one missing thing.
    if (toProblem(headroom.error).status === 404) return null
    return <QueryErrorBanner error={headroom.error} />
  }
  if (headroom.isLoading || !headroom.data) return <Skeleton className="h-48 w-full" />
  const hasCeilings = headroom.data.thresholds.length > 0
  const contracts = capped.data ?? []
  if (!hasCeilings) {
    // Without ceilings there is only the contracts' caps to show: wait for them, say why
    // they could not be read, and draw nothing when no active contract has one.
    if (capped.isPending) return <Skeleton className="h-48 w-full" />
    if (capped.isError) return <QueryErrorBanner error={capped.error} />
    if (contracts.length === 0) return null
  }

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="font-semibold">
            {hasCeilings ? `Soglie del forfettario ${year}` : 'Tetti dei contratti'}
            {title ? ` · ${title}` : ''}
          </h2>
          {hasCeilings ? (
            <p className="text-sm text-muted-foreground">
              Contano gli incassi dell’anno solare, rivalsa compresa: è quanto resta da incassare
              prima di ciascuna soglia.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Alcuni contratti attivi limitano la quota del cliente sui ricavi fatturati
              dell’azienda, sull’anno di anniversario del contratto.
            </p>
          )}
        </div>
        {hasCeilings ? (
          <div className="divide-y">
            {headroom.data.thresholds.map((ceiling) => (
              <CeilingRow key={ceiling.id} ceiling={ceiling} />
            ))}
          </div>
        ) : null}
        {/* With ceilings the page still draws; a contracts read that failed must not look like
            «no contract has a cap», or «Ci sta» would be answered from the ceilings alone. */}
        {hasCeilings && capped.isError ? <QueryErrorBanner error={capped.error} /> : null}
        {/* Keyed on the year so a new period starts the form over: a date typed for 2026
            means nothing against 2027's room. */}
        <Simulator
          key={`${year}:${legal_entity ?? ''}`}
          year={year}
          legalEntityId={legalEntityId}
          hasCeilings={hasCeilings}
          contracts={contracts}
        />
      </CardContent>
    </Card>
  )
}
