import { StatusPill } from '@/components/StatusPill'
import { formatDate } from '@/features/invoices/format'
import { Meter } from './charts'
import { FILL_TONE, consequence, fill, magnitude, sharePercent } from './ceilingFormat'
import { money } from './format'
import type { CeilingSimulation } from './queries'

/**
 * The contract's cap read against the addition: the customer's share of the azienda's
 * invoiced revenue over the contract's anniversary year, before and after, with the cap
 * on the same bar. Every figure is the server's (`contract_cap`); this prints them.
 */
function CapVerdict({ cap }: { cap: NonNullable<CeilingSimulation['contract_cap']> }) {
  return (
    <div role="group" aria-label={`Tetto di ${cap.title}`} className="space-y-1.5">
      <p className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="font-medium">
          Tetto del contratto «{cap.title}» · anno dal {formatDate(cap.period_from)} al{' '}
          {formatDate(cap.period_to)}
        </span>
        <StatusPill tone={cap.falls_within ? 'ink' : 'danger'}>
          {cap.falls_within ? 'Ci sta' : 'Non ci sta'}
        </StatusPill>
      </p>
      <div className="flex items-center gap-3">
        <Meter
          ratio={cap.simulated_share}
          tone={FILL_TONE}
          color={fill(!cap.falls_within, null)}
          mark={cap.cap}
        />
        <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
          {sharePercent.format(cap.cap)}
        </span>
      </div>
      <p className="text-sm">
        Il cliente passerebbe dal {sharePercent.format(cap.current_share)} al{' '}
        {sharePercent.format(cap.simulated_share)} dei ricavi, tetto {sharePercent.format(cap.cap)}.
      </p>
    </div>
  )
}

/**
 * The answer to «ci sta?». The overall verdict is the conjunction of every ceiling and of
 * the contract's cap: a pack ceiling with room left must not say «Ci sta.» over a cap the
 * addition would reach. With no ceilings (the ordinary regime) the cap is all there is.
 */
export function Verdict({ simulation }: { simulation: CeilingSimulation }) {
  const cap = simulation.contract_cap ?? null
  if (simulation.thresholds.length === 0 && cap === null) return null
  const fits = simulation.thresholds.every((ceiling) => ceiling.falls_within) && (cap === null || cap.falls_within)
  return (
    <div className="space-y-2 border-t pt-4">
      <p className="text-base font-semibold">
        {fits ? 'Ci sta.' : 'Non ci sta.'}{' '}
        <span className="font-normal">
          {simulation.thresholds.length > 0
            ? `Aggiungendo ${money(simulation.brief_addition)} ai ricavi incassati del ${simulation.year}:`
            : `Aggiungendo ${money(simulation.brief_addition)} ai ricavi del cliente:`}
        </span>
      </p>
      <ul className="space-y-3">
        {simulation.thresholds.map((ceiling) => (
          <li key={ceiling.id} className="space-y-1.5">
            <p className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
              <span className="font-medium">{ceiling.label}</span>
              <StatusPill tone={ceiling.falls_within ? 'ink' : 'danger'}>
                {ceiling.falls_within ? 'Rientra' : 'Non rientra'}
              </StatusPill>
            </p>
            <div className="flex items-center gap-3">
              <Meter
                ratio={ceiling.simulated_usage}
                tone={FILL_TONE}
                color={fill(!ceiling.falls_within, ceiling.alert_level_simulated)}
              />
              <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
                {money(ceiling.threshold)}
              </span>
            </div>
            <p className="text-sm">
              {money(ceiling.current_revenue)} diventano {money(ceiling.simulated_revenue)}:{' '}
              {ceiling.falls_within
                ? `restano ${magnitude(ceiling.simulated_remaining)}`
                : `superata di ${magnitude(ceiling.simulated_remaining)}`}
              {ceiling.alert_level_simulated ? ` · ${ceiling.alert_level_simulated}` : ''}.
            </p>
            {!ceiling.falls_within ? (
              <p className="text-xs text-muted-foreground">
                {consequence(ceiling.consequence, true)}
              </p>
            ) : null}
          </li>
        ))}
        {cap !== null ? (
          <li>
            <CapVerdict cap={cap} />
          </li>
        ) : null}
      </ul>
    </div>
  )
}
