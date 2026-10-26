import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { RadioGroup } from 'radix-ui'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { useDeals, useStages, type Deal } from '@/features/deals/queries'
import { useLegalEntity } from '@/lib/legalEntity'
import { toProblem } from '@/lib/api'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Skeleton } from '@rebase/ui/skeleton'
import { Meter } from './charts'
import { money } from './format'
import {
  useCeilingHeadroom,
  useCeilingSimulation,
  type CeilingSimulation,
  type CeilingStatus,
} from './queries'
import { simulatorRequest, yearOfIso, type SimulatorFields } from './simulatorInput'

/**
 * What crossing a ceiling does, in the words the regime uses. Keyed on the API's closed
 * `conseguenza` enum, so a third consequence makes this map fail to compile instead of
 * printing nothing. «Se la superi» before the ceiling is crossed and «Superata» after it:
 * the same sentence under a ceiling with room left would say it already happened.
 */
const CONSEGUENZA: Record<CeilingStatus['conseguenza'], string> = {
  esce_dall_anno_successivo: 'si esce dal regime forfettario dall’anno successivo.',
  esce_immediatamente: 'si esce dal regime forfettario subito, nell’anno stesso.',
}

function consequence(ceiling: CeilingStatus['conseguenza'], crossed: boolean): string {
  return `${crossed ? 'Superata:' : 'Se la superi,'} ${CONSEGUENZA[ceiling]}`
}

/** The fill of a bar with room left is the ink the other charts use for what was collected
 *  (`--chart-2`), never the accent hue, which reads as a warning. One that has crossed its
 *  ceiling is the destructive tint; one inside an alert level is that tint mixed into the
 *  ordinary ink. */
const FILL_TONE = 2
const FILL_CROSSED = 'var(--destructive)'
const FILL_ALERT = 'color-mix(in oklab, var(--destructive) 45%, var(--chart-2))'

function fill(superata: boolean, alert: string | null): string | undefined {
  if (superata) return FILL_CROSSED
  return alert === null ? undefined : FILL_ALERT
}

/** «25.000,00 €» out of a signed decimal string: the sign is the sentence's job, never
 *  a minus in front of a figure that reads «superata di». */
function magnitude(value: string): string {
  return money(value.replace(/^-/, ''))
}

function CeilingRow({ ceiling }: { ceiling: CeilingStatus }) {
  const crossed = ceiling.superata
  return (
    <div role="group" aria-label={ceiling.etichetta} className="space-y-2 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">{ceiling.etichetta}</p>
        {crossed ? (
          <StatusPill tone="danger">Superata</StatusPill>
        ) : ceiling.livello_allerta ? (
          <StatusPill tone="gold">{ceiling.livello_allerta}</StatusPill>
        ) : null}
      </div>
      <div className="flex items-center gap-3">
        <Meter
          ratio={ceiling.utilizzo}
          tone={FILL_TONE}
          color={fill(crossed, ceiling.livello_allerta)}
        />
        <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
          {money(ceiling.soglia)}
        </span>
      </div>
      <p className="text-sm">
        Incassati {money(ceiling.ricavi)} su {money(ceiling.soglia)}:{' '}
        <strong className="font-semibold">
          {crossed
            ? `superata di ${magnitude(ceiling.residuo)}`
            : `restano ${magnitude(ceiling.residuo)}`}
        </strong>
        .
      </p>
      <p className="text-xs text-muted-foreground">
        {consequence(ceiling.conseguenza, crossed)}
      </p>
    </div>
  )
}

/** The estimate a deal carries, if it carries one: the same two shapes the simulator takes. */
function hasEstimate(deal: Deal): boolean {
  return (
    deal.valore_preventivato !== null ||
    (deal.ore_preventivate !== null && deal.tariffa_oraria !== null)
  )
}

function estimateOf(deal: Deal): string {
  if (deal.valore_preventivato !== null) return money(deal.valore_preventivato)
  return `${deal.ore_preventivate?.replace('.', ',')} ore a ${money(deal.tariffa_oraria ?? '0')}`
}

/**
 * The open deals of the azienda, to pick one from. «Open» by the pipeline stage's own
 * `tipo`, never by its name: a space renames its stages. A won deal is already revenue
 * (or about to be invoiced) and a lost one will never be, so neither is a «what if».
 */
function DealPicker({
  id,
  aziendaId,
  selectedId,
  onSelect,
  onCurrent,
}: {
  id: string
  aziendaId?: string
  selectedId: string
  onSelect: (id: string) => void
  /** Told the deal as the list has it now, so that «Simula» sends the estimate the picker
   *  shows and not the one it had when the deal was chosen. */
  onCurrent: (deal: Deal | null) => void
}) {
  const deals = useDeals(aziendaId ? { azienda_id: aziendaId } : {})
  const stages = useStages()
  const current = deals.data?.items.find((deal) => deal.id === selectedId) ?? null
  useEffect(() => {
    onCurrent(current)
  }, [onCurrent, current])
  if (deals.isError) return <QueryErrorBanner error={deals.error} />
  if (stages.isError) return <QueryErrorBanner error={stages.error} />
  if (deals.isPending || stages.isPending || !deals.data || !stages.data) {
    return <Skeleton className="h-9 w-full" />
  }
  const open = new Set(stages.data.filter((stage) => stage.tipo === 'open').map((stage) => stage.id))
  const candidates = deals.data.items.filter((deal) => open.has(deal.pipeline_stage_id))
  if (candidates.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Nessun deal aperto: scegli «Un importo» e scrivi la stima.
      </p>
    )
  }
  const chosen = candidates.find((deal) => deal.id === selectedId)
  return (
    <div className="space-y-1">
      <Select
        value={selectedId}
        onValueChange={onSelect}
      >
        <SelectTrigger id={id} className="w-full" aria-label="Deal">
          <SelectValue placeholder="Scegli un deal" />
        </SelectTrigger>
        <SelectContent>
          {candidates.map((deal) => (
            <SelectItem key={deal.id} value={deal.id} disabled={!hasEstimate(deal)}>
              {deal.nome}
              {deal.customer_ragione_sociale ? ` · ${deal.customer_ragione_sociale}` : ''}
              {hasEstimate(deal) ? '' : ' (senza stima)'}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {chosen ? (
        <p className="text-xs text-muted-foreground">Stima del deal: {estimateOf(chosen)}</p>
      ) : null}
    </div>
  )
}

function Verdict({ simulation }: { simulation: CeilingSimulation }) {
  if (simulation.soglie.length === 0) return null
  const fits = simulation.soglie.every((ceiling) => ceiling.rientra)
  return (
    <div className="space-y-2 border-t pt-4">
      <p className="text-base font-semibold">
        {fits ? 'Ci sta.' : 'Non ci sta.'}{' '}
        <span className="font-normal">
          Aggiungendo {money(simulation.aggiunta_sintetica)} ai ricavi incassati del{' '}
          {simulation.anno}:
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
      </ul>
    </div>
  )
}

const SOURCES = [
  { id: 'deal', label: 'Un deal' },
  { id: 'importo', label: 'Un importo' },
] as const

/**
 * «Ci sta?»: a not-yet-won job's estimate, added to the year's real receipts and read
 * against every ceiling, with nothing saved. The estimate is a deal's own, or an amount
 * (or hours and a rate) typed here; the date is when the money would arrive, because the
 * ceiling counts what is collected in a calendar year, and its year is the year asked.
 *
 * Asks on «Simula», not on every keystroke, and keeps the answer only while it is on
 * screen. The server does the sums (REB-373); this form translates what an Italian
 * keyboard produces into the decimal the API reads and prints what comes back.
 */
function Simulator({ anno, aziendaId }: { anno: number; aziendaId?: string }) {
  const [source, setSource] = useState<SimulatorFields['source']>('importo')
  const [dealId, setDealId] = useState('')
  const latestDeal = useRef<Deal | null>(null)
  const trackDeal = useCallback((current: Deal | null) => {
    latestDeal.current = current
  }, [])
  const [valore, setValore] = useState('')
  const [ore, setOre] = useState('')
  const [tariffa, setTariffa] = useState('')
  const [data, setData] = useState(`${anno}-12-31`)
  const uid = useId()
  const [asked, setAsked] = useState<{
    anno: number
    input: NonNullable<Parameters<typeof useCeilingSimulation>[1]>
  } | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const simulation = useCeilingSimulation(asked?.anno ?? anno, asked?.input ?? null, aziendaId)

  // A verdict answers the inputs it was asked with: the moment one of them changes it is
  // no longer an answer to what is on screen, so it goes until «Simula» is pressed again.
  const edit = <T,>(set: (value: T) => void) => (value: T) => {
    set(value)
    setAsked(null)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const request = simulatorRequest({
      source,
      valore,
      ore,
      tariffa,
      deal: dealId === '' ? null : latestDeal.current,
    })
    const year = yearOfIso(data)
    if (!request.ok || year === null) {
      setErrors({
        ...(request.ok ? {} : request.errors),
        ...(year === null ? { data: 'Scegli una data tra il 2000 e il 2200.' } : {}),
      })
      setAsked(null)
      return
    }
    setErrors({})
    setAsked({ anno: year, input: request.input })
  }

  return (
    <form onSubmit={submit} className="space-y-4 border-t pt-4" noValidate>
      <div>
        <h3 className="font-semibold">Ci sta?</h3>
        <p className="text-sm text-muted-foreground">
          Aggiungi ai ricavi incassati dell’anno la stima di un lavoro e guarda se resti sotto le
          soglie. Non viene salvato niente.
        </p>
      </div>

      <RadioGroup.Root
        value={source}
        onValueChange={(next) => {
          setSource(next as SimulatorFields['source'])
          setErrors({})
          setAsked(null)
        }}
        aria-label="Da dove viene la stima"
        className="flex items-center gap-1"
      >
        {SOURCES.map((candidate) => (
          <RadioGroup.Item key={candidate.id} value={candidate.id} asChild>
            <Button variant={candidate.id === source ? 'default' : 'outline'} size="sm">
              {candidate.label}
            </Button>
          </RadioGroup.Item>
        ))}
      </RadioGroup.Root>

      {source === 'deal' ? (
        <div className="space-y-2">
          <Label htmlFor={`${uid}-deal`}>Deal</Label>
          <DealPicker
            id={`${uid}-deal`}
            aziendaId={aziendaId}
            selectedId={dealId}
            onSelect={edit(setDealId)}
            onCurrent={trackDeal}
          />
          {errors.deal ? <FieldError>{errors.deal}</FieldError> : null}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor={`${uid}-valore`}>Importo</Label>
            <Input
              id={`${uid}-valore`}
              inputMode="decimal"
              placeholder="12.000,00"
              value={valore}
              onChange={(event) => edit(setValore)(event.target.value)}
              aria-invalid={errors.valore ? true : undefined}
            />
            {errors.valore ? <FieldError>{errors.valore}</FieldError> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${uid}-ore`}>oppure Ore</Label>
            <Input
              id={`${uid}-ore`}
              inputMode="decimal"
              placeholder="100"
              value={ore}
              onChange={(event) => edit(setOre)(event.target.value)}
              aria-invalid={errors.ore ? true : undefined}
            />
            {errors.ore ? <FieldError>{errors.ore}</FieldError> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${uid}-tariffa`}>per Tariffa oraria</Label>
            <Input
              id={`${uid}-tariffa`}
              inputMode="decimal"
              placeholder="80,00"
              value={tariffa}
              onChange={(event) => edit(setTariffa)(event.target.value)}
              aria-invalid={errors.tariffa ? true : undefined}
            />
            {errors.tariffa ? <FieldError>{errors.tariffa}</FieldError> : null}
          </div>
          {errors.form ? (
            <div className="sm:col-span-3">
              <FieldError>{errors.form}</FieldError>
            </div>
          ) : null}
        </div>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-2">
          <Label htmlFor={`${uid}-data`}>Incasso previsto il</Label>
          <Input
            id={`${uid}-data`}
            type="date"
            value={data}
            onChange={(event) => edit(setData)(event.target.value)}
            className="w-44"
            aria-invalid={errors.data ? true : undefined}
          />
          <p className="text-xs text-muted-foreground">Conta l’anno solare dell’incasso.</p>
          {errors.data ? <FieldError>{errors.data}</FieldError> : null}
        </div>
        <Button type="submit" disabled={simulation.isFetching}>
          Simula
        </Button>
      </div>

      {/* Always in the page, so a screen reader that has registered it announces what lands
          in it; a live region that appears already full is read by nobody. */}
      <div role="status" aria-live="polite">
        {simulation.isError && asked !== null ? <QueryErrorBanner error={simulation.error} /> : null}
        {simulation.isFetching && asked !== null && !simulation.data ? (
          <Skeleton className="h-24 w-full" />
        ) : null}
        {simulation.data && asked !== null ? <Verdict simulation={simulation.data} /> : null}
      </div>
    </form>
  )
}

function FieldError({ children }: { children: string }) {
  return (
    <p role="alert" className="text-xs text-destructive">
      {children}
    </p>
  )
}

/**
 * Room left under each ceiling of the forfettario, and the «ci sta?» simulator under it
 * (REB-755). What the server answers decides everything: an azienda the thresholds do not
 * apply to (the ordinary regime, a company abroad) gets an empty list and this draws
 * nothing at all, not an empty box that says it has nothing.
 *
 * Beside the estimate, one azienda's and read from its own `anno`, which is the year the
 * dashboard's period names. In «tutte» the tab draws one panel per azienda, and one without
 * a profile (the estimate card says so once) or without ceilings draws nothing.
 */
export function CeilingPanel({
  anno,
  aziendaId,
  titolo,
}: {
  anno: number
  /** One azienda's ceilings, by id; omitted, the sidebar's selection. */
  aziendaId?: string
  /** Shown beside the year in the heading: the azienda's name in «tutte». */
  titolo?: string
}) {
  const headroom = useCeilingHeadroom(anno, aziendaId)
  // The azienda the figures are of: the block's own, else the sidebar's. It keys the
  // simulator, so switching the sidebar starts the form over instead of asking the new
  // azienda a question typed for the old one.
  const { selected } = useLegalEntity()
  const azienda = aziendaId ?? selected

  if (headroom.isError) {
    // No profile (404): the estimate card above already says so, in the place that owns
    // the settings link. Saying it twice would be two prompts for one missing thing.
    if (toProblem(headroom.error).status === 404) return null
    return <QueryErrorBanner error={headroom.error} />
  }
  if (headroom.isLoading || !headroom.data) return <Skeleton className="h-48 w-full" />
  if (headroom.data.soglie.length === 0) return null

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="font-semibold">
            Soglie del forfettario {anno}
            {titolo ? ` · ${titolo}` : ''}
          </h2>
          <p className="text-sm text-muted-foreground">
            Contano gli incassi dell’anno solare, rivalsa compresa: è quanto resta da incassare
            prima di ciascuna soglia.
          </p>
        </div>
        <div className="divide-y">
          {headroom.data.soglie.map((ceiling) => (
            <CeilingRow key={ceiling.id} ceiling={ceiling} />
          ))}
        </div>
        {/* Keyed on the year so a new period starts the form over: a date typed for 2026
            means nothing against 2027's room. */}
        <Simulator key={`${anno}:${azienda ?? ''}`} anno={anno} aziendaId={aziendaId} />
      </CardContent>
    </Card>
  )
}
