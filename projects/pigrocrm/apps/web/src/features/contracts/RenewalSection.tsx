import { useState, type ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { LoadingState } from '@/components/LoadingState'
import { formatDate } from '@/features/invoices/format'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { currencySign, formatAmount } from './labels'
import {
  useContractConcentration,
  useContractProjection,
  useRenewalAssumption,
  useSetRenewalAssumption,
  type Contract,
  type RenewalAssumption,
} from './queries'

const share = new Intl.NumberFormat('it-IT', { style: 'percent', maximumFractionDigits: 1 })

/** `iso` plus `days` calendar days, as `YYYY-MM-DD`. Built from the parts so a daylight
 *  saving change cannot move the day. */
function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split('-').map(Number)
  return toIsoDate(new Date(year ?? 0, (month ?? 1) - 1, (day ?? 1) + days))
}

function addMonths(iso: string, months: number): string {
  const [year, month, day] = iso.split('-').map(Number)
  return toIsoDate(new Date(year ?? 0, (month ?? 1) - 1 + months, day ?? 1))
}

function Figure({
  label,
  value,
  hint,
  aside,
}: {
  label: string
  value: string
  hint?: string
  /** What sits on the figure's line, after the value: a cap and where the figure stands. */
  aside?: ReactNode
}) {
  return (
    <div>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-lg font-semibold tabular-nums">{value}</span>
          {aside}
        </div>
        {hint !== undefined && <p className="text-xs text-muted-foreground">{hint}</p>}
      </dd>
    </div>
  )
}

interface AssumptionFormProps {
  contractId: string
  currency: string
  assumption: RenewalAssumption | null
  canWrite: boolean
}

/** The renewal assumption as the three facts it records. The fields are seeded from the
 *  stored row once per version of it (`key` on `updated_at` in the caller), so a save
 *  from another tab does not leave stale text in the boxes. */
function AssumptionForm({ contractId, currency, assumption, canWrite }: AssumptionFormProps) {
  const [probability, setProbability] = useState(assumption?.probability.toString() ?? '')
  const [volume, setVolume] = useState(assumption === null ? '' : String(assumption.expected_volume))
  const [horizon, setHorizon] = useState(assumption?.horizon_until ?? '')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const save = useSetRenewalAssumption(contractId)

  function submit() {
    setProblem(null)
    save.mutate(
      {
        // Left as typed when it is not a whole number: the server's own 422 names it.
        probability: /^\d+$/.test(probability.trim())
          ? Number(probability.trim())
          : (probability.trim() as unknown as number),
        expected_volume: volume.trim().replace(',', '.'),
        horizon_until: horizon,
      },
      {
        onSuccess: () => toast.success('Assunzione di rinnovo salvata'),
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  const elsewhere = problem !== null && fieldError === null ? problem.detail : null

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="renewal-probability">Probabilità di rinnovo (%)</Label>
        <Input
          id="renewal-probability"
          type="number"
          min={0}
          max={100}
          value={probability}
          disabled={!canWrite}
          onChange={(event) => setProbability(event.target.value)}
          aria-invalid={fieldError?.field === 'probability'}
        />
        {errorOn('probability')}
      </div>
      <div className="space-y-2">
        <Label htmlFor="renewal-volume">Volume atteso ({currencySign(currency)})</Label>
        <Input
          id="renewal-volume"
          inputMode="decimal"
          placeholder="12000.00"
          value={volume}
          disabled={!canWrite}
          onChange={(event) => setVolume(event.target.value)}
          aria-invalid={fieldError?.field === 'expected_volume'}
        />
        {errorOn('expected_volume')}
      </div>
      <div className="space-y-2">
        <Label htmlFor="renewal-horizon">Orizzonte</Label>
        <Input
          id="renewal-horizon"
          type="date"
          value={horizon}
          disabled={!canWrite}
          onChange={(event) => setHorizon(event.target.value)}
          aria-invalid={fieldError?.field === 'horizon_until'}
        />
        <p className="text-xs text-muted-foreground">
          Fino a quando vale il volume atteso dopo la fine del termine noto.
        </p>
        {errorOn('horizon_until')}
      </div>
      {elsewhere !== null && <p className="text-sm text-destructive">{elsewhere}</p>}
      {canWrite && (
        <Button onClick={submit} disabled={save.isPending}>
          {save.isPending ? 'Salvataggio…' : 'Salva assunzione'}
        </Button>
      )}
    </div>
  )
}

/**
 * The «Rinnovo e ricavi programmati» section of a contract's page (REB-744): the renewal
 * assumption to set and change, the revenue the contract programs beyond its
 * irrevocability window over a window of dates, and the customer's share of the
 * revenue on the contract's own anniversary year.
 *
 * Every figure is the API's: the programmed revenue and its renewal share come from
 * `GET /api/contracts/{id}/projection` and the share from `.../concentration`, and
 * nothing here adds or scales an amount. The window is the person's to move: it
 * starts today and runs to the day after the assumption's horizon (or the contract's
 * end, or a year on), because those are the dates the figure is about.
 */
export function RenewalSection({ contract }: { contract: Contract }) {
  const canWrite = useCan('set_renewal_assumption')
  const assumption = useRenewalAssumption(contract.id)
  const today = toIsoDate(new Date())
  const [chosenEnd, setChosenEnd] = useState<string | null>(null)

  const defaultEnd =
    assumption.data != null
      ? addDays(assumption.data.horizon_until, 1)
      : contract.end !== null
        ? addDays(contract.end, 1)
        : addMonths(today, 12)
  const end = chosenEnd ?? defaultEnd
  const validWindow = end > today

  const projection = useContractProjection(contract.id, { from: today, to: end }, validWindow)
  const started = contract.start <= today
  const concentration = useContractConcentration(contract.id, started)

  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="mb-3 font-semibold">Rinnovo e ricavi programmati</h2>

        {assumption.isLoading ? (
          <LoadingState />
        ) : assumption.isError ? (
          <QueryErrorBanner error={assumption.error} />
        ) : (
          <div className="grid gap-8 lg:grid-cols-2">
            <div>
              <h3 className="mb-3 text-sm font-medium">Assunzione di rinnovo</h3>
              {assumption.data === null && (
                <p className="mb-3 text-sm text-muted-foreground">
                  Nessuna assunzione registrata: senza una, oltre il termine il contratto non
                  programma nulla.
                </p>
              )}
              <AssumptionForm
                key={assumption.data?.updated_at ?? 'none'}
                contractId={contract.id}
                currency={contract.currency}
                assumption={assumption.data ?? null}
                canWrite={canWrite}
              />
            </div>

            <div className="space-y-6">
              <div>
                <h3 className="mb-3 text-sm font-medium">Ricavi programmati</h3>
                <div className="mb-3 max-w-xs space-y-2">
                  <Label htmlFor="renewal-until">Fino al (escluso)</Label>
                  <Input
                    id="renewal-until"
                    type="date"
                    value={end}
                    onChange={(event) => setChosenEnd(event.target.value)}
                  />
                </div>
                {!validWindow ? (
                  <p className="text-sm text-muted-foreground">Scegli una data dopo oggi.</p>
                ) : projection.isLoading ? (
                  <LoadingState />
                ) : projection.isError ? (
                  <QueryErrorBanner error={projection.error} />
                ) : projection.data !== undefined ? (
                  <dl className="grid grid-cols-3 gap-4">
                    <Figure
                      label="Programmato"
                      value={formatAmount(projection.data.scheduled, contract.currency)}
                      hint={
                        projection.data.irrevocability_window_until_to === null
                          ? 'Contratto già concluso'
                          : `Oltre il ${formatDate(projection.data.irrevocability_window_until_to)}`
                      }
                    />
                    <Figure label="Da rinnovo" value={formatAmount(projection.data.from_renewal, contract.currency)} />
                    <Figure label="Totale" value={formatAmount(projection.data.total, contract.currency)} />
                  </dl>
                ) : null}
              </div>

              <div>
                <h3 className="mb-3 text-sm font-medium">Quota del cliente</h3>
                {!started ? (
                  <p className="text-sm text-muted-foreground">
                    Il contratto inizia il {formatDate(contract.start)}: l’anno di riferimento non
                    è ancora cominciato.
                  </p>
                ) : concentration.isLoading ? (
                  <LoadingState />
                ) : concentration.isError ? (
                  <QueryErrorBanner error={concentration.error} />
                ) : concentration.data !== undefined ? (
                  <dl className="space-y-3">
                    {concentration.data.currencies.map((row) => (
                      <Figure
                        key={row.currency}
                        label={`Anno dal ${formatDate(concentration.data.period_from)} al ${formatDate(concentration.data.period_to)}${concentration.data.currencies.length > 1 ? ` (${row.currency})` : ''}`}
                        value={share.format(row.share)}
                        hint={`${formatAmount(row.customer_revenue, row.currency)} su ${formatAmount(row.total_revenue, row.currency)} fatturati`}
                        aside={
                          concentration.data.threshold_from_contract &&
                          concentration.data.threshold != null ? (
                            <>
                              <span className="text-sm text-muted-foreground">
                                Tetto {share.format(concentration.data.threshold)}
                              </span>
                              <StatusPill tone={row.exceeded ? 'danger' : 'ink'}>
                                {row.exceeded ? 'Tetto raggiunto' : 'Sotto il tetto'}
                              </StatusPill>
                            </>
                          ) : undefined
                        }
                      />
                    ))}
                  </dl>
                ) : null}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
