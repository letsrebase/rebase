import { StatusPill } from '@/components/StatusPill'
import { formatDate } from '@/features/invoices/format'
import { Meter } from './charts'
import { FILL_TONE, consequence, fill, magnitude, sharePercent } from './ceilingFormat'
import { money } from './format'
import type { CeilingSimulation } from './queries'

/**
 * The contract's cap read against the addition: the customer's share of the azienda's
 * invoiced revenue over the contract's anniversary year, before and after, with the cap
 * on the same bar. Every figure is the server's (`tetto_contratto`); this prints them.
 */
function CapVerdict({ cap }: { cap: NonNullable<CeilingSimulation['tetto_contratto']> }) {
  return (
    <div role="group" aria-label={`Tetto di ${cap.titolo}`} className="space-y-1.5">
      <p className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="font-medium">
          Tetto del contratto «{cap.titolo}» · anno dal {formatDate(cap.periodo_da)} al{' '}
          {formatDate(cap.periodo_a)}
        </span>
        <StatusPill tone={cap.rientra ? 'ink' : 'danger'}>
          {cap.rientra ? 'Ci sta' : 'Non ci sta'}
        </StatusPill>
      </p>
      <div className="flex items-center gap-3">
        <Meter
          ratio={cap.quota_simulata}
          tone={FILL_TONE}
          color={fill(!cap.rientra, null)}
          mark={cap.tetto}
        />
        <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
          {sharePercent.format(cap.tetto)}
        </span>
      </div>
      <p className="text-sm">
        Il cliente passerebbe dal {sharePercent.format(cap.quota_attuale)} al{' '}
        {sharePercent.format(cap.quota_simulata)} dei ricavi, tetto {sharePercent.format(cap.tetto)}.
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
  const cap = simulation.tetto_contratto ?? null
  if (simulation.soglie.length === 0 && cap === null) return null
  const fits = simulation.soglie.every((ceiling) => ceiling.rientra) && (cap === null || cap.rientra)
  return (
    <div className="space-y-2 border-t pt-4">
      <p className="text-base font-semibold">
        {fits ? 'Ci sta.' : 'Non ci sta.'}{' '}
        <span className="font-normal">
          {simulation.soglie.length > 0
            ? `Aggiungendo ${money(simulation.aggiunta_sintetica)} ai ricavi incassati del ${simulation.anno}:`
            : `Aggiungendo ${money(simulation.aggiunta_sintetica)} ai ricavi del cliente:`}
        </span>
      </p>
      <ul className="space-y-3">
        {simulation.soglie.map((ceiling) => (
          <li key={ceiling.id} className="space-y-1.5">
            <p className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
              <span className="font-medium">{ceiling.etichetta}</span>
              <StatusPill tone={ceiling.rientra ? 'ink' : 'danger'}>
                {ceiling.rientra ? 'Rientra' : 'Non rientra'}
              </StatusPill>
            </p>
            <div className="flex items-center gap-3">
              <Meter
                ratio={ceiling.utilizzo_simulato}
                tone={FILL_TONE}
                color={fill(!ceiling.rientra, ceiling.livello_allerta_simulato)}
              />
              <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
                {money(ceiling.soglia)}
              </span>
            </div>
            <p className="text-sm">
              {money(ceiling.ricavi_attuali)} diventano {money(ceiling.ricavi_simulati)}:{' '}
              {ceiling.rientra
                ? `restano ${magnitude(ceiling.residuo_simulato)}`
                : `superata di ${magnitude(ceiling.residuo_simulato)}`}
              {ceiling.livello_allerta_simulato ? ` · ${ceiling.livello_allerta_simulato}` : ''}.
            </p>
            {!ceiling.rientra ? (
              <p className="text-xs text-muted-foreground">
                {consequence(ceiling.conseguenza, true)}
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
