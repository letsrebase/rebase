import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { RadioGroup } from 'radix-ui'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { useDeals, useStages, type Deal } from '@/features/deals/queries'
import { formatCapPercent } from '@/features/contracts/labels'
import type { Contract } from '@/features/contracts/queries'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Skeleton } from '@rebase/ui/skeleton'
import { Verdict } from './CeilingVerdict'
import { money } from './format'
import { useCeilingSimulation } from './queries'
import { simulatorRequest, yearOfIso, type SimulatorFields } from './simulatorInput'

/** The estimate a deal carries, if it carries one: the same two shapes the simulator takes. */
function hasEstimate(deal: Deal): boolean {
  return (
    deal.budgeted_value !== null ||
    (deal.budgeted_hours !== null && deal.hourly_rate !== null)
  )
}

function estimateOf(deal: Deal): string {
  if (deal.budgeted_value !== null) return money(deal.budgeted_value)
  return `${deal.budgeted_hours?.replace('.', ',')} ore a ${money(deal.hourly_rate ?? '0')}`
}

/**
 * The open deals of the azienda, to pick one from. «Open» by the pipeline stage's own
 * `type`, never by its name: a space renames its stages. A won deal is already revenue
 * (or about to be invoiced) and a lost one will never be, so neither is a «what if».
 */
function DealPicker({
  id,
  legalEntityId,
  selectedId,
  onSelect,
  onCurrent,
}: {
  id: string
  legalEntityId?: string
  selectedId: string
  onSelect: (id: string) => void
  /** Told the deal as the list has it now, so that «Simula» sends the estimate the picker
   *  shows and not the one it had when the deal was chosen. */
  onCurrent: (deal: Deal | null) => void
}) {
  const deals = useDeals(legalEntityId ? { legal_entity_id: legalEntityId } : {})
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
  const open = new Set(stages.data.filter((stage) => stage.type === 'open').map((stage) => stage.id))
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
              {deal.customer_legal_name ? ` · ${deal.customer_legal_name}` : ''}
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

const NO_CONTRACT = '__nessuno__'

/**
 * The active contracts of the azienda that cap the customer's share, to read the same
 * addition against. Optional beside the ceilings; the only thing to ask when the azienda
 * has none (`required`).
 */
function ContractPicker({
  id,
  contracts,
  value,
  required,
  onSelect,
}: {
  id: string
  contracts: Contract[]
  value: string
  required: boolean
  onSelect: (id: string) => void
}) {
  const chosen = contracts.find((contract) => contract.id === value)
  return (
    <div className="space-y-1">
      <Select
        value={value}
        onValueChange={(next) => onSelect(next === NO_CONTRACT ? '' : next)}
      >
        <SelectTrigger id={id} className="w-full" aria-label="Contratto con un tetto">
          <SelectValue placeholder={required ? 'Scegli un contratto' : 'Nessun contratto'} />
        </SelectTrigger>
        <SelectContent>
          {required ? null : <SelectItem value={NO_CONTRACT}>Nessun contratto</SelectItem>}
          {contracts.map((contract) => (
            <SelectItem key={contract.id} value={contract.id}>
              {contract.title}
              {contract.customer_share_cap === null
                ? ''
                : ` · tetto ${formatCapPercent(contract.customer_share_cap)}`}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {chosen ? (
        <p className="text-xs text-muted-foreground">
          Letto sull’anno di anniversario del contratto che contiene la data dell’incasso.
        </p>
      ) : null}
    </div>
  )
}

const SOURCES = [
  { id: 'deal', label: 'Un deal' },
  { id: 'amount', label: 'Un importo' },
] as const

/**
 * «Ci sta?»: a not-yet-won job's estimate, added to the year's real receipts and read
 * against every ceiling, with nothing saved. The estimate is a deal's own, or an amount
 * (or hours and a rate) typed here; the date is when the money would arrive, because the
 * ceiling counts what is collected in a calendar year, and its year is the year asked.
 *
 * A contract with a cap on the customer's share (REB-761) can be chosen too: the same
 * addition and date are then read against that cap, over the contract's own anniversary
 * year. Where the azienda has no ceilings (`hasCeilings` false) the cap is the only thing
 * to read, so the contract is required.
 *
 * Asks on «Simula», not on every keystroke, and keeps the answer only while it is on
 * screen. The server does the sums (REB-373); this form translates what an Italian
 * keyboard produces into the decimal the API reads and prints what comes back.
 */
export function Simulator({
  year: anno,
  legalEntityId,
  hasCeilings,
  contracts,
}: {
  year: number
  legalEntityId?: string
  hasCeilings: boolean
  contracts: Contract[]
}) {
  const [source, setSource] = useState<SimulatorFields['source']>('amount')
  const [dealId, setDealId] = useState('')
  const [contractId, setContractId] = useState('')
  const attempts = useRef(0)
  const latestDeal = useRef<Deal | null>(null)
  const trackDeal = useCallback((current: Deal | null) => {
    latestDeal.current = current
  }, [])
  const [valore, setValue] = useState('')
  const [hours, setHours] = useState('')
  const [rate, setRate] = useState('')
  const [data, setData] = useState(`${anno}-12-31`)
  const uid = useId()
  const [asked, setAsked] = useState<{
    year: number
    input: NonNullable<Parameters<typeof useCeilingSimulation>[1]>
    attempt: number
  } | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const simulation = useCeilingSimulation(
    asked?.year ?? anno,
    asked?.input ?? null,
    legalEntityId,
    asked?.attempt,
  )

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
      value: valore,
      hours,
      rate,
      deal: dealId === '' ? null : latestDeal.current,
      data,
      contractId,
    })
    const missingContract = !hasCeilings && contractId === ''
    const year = yearOfIso(data)
    if (!request.ok || year === null || missingContract) {
      setErrors({
        ...(request.ok ? {} : request.errors),
        ...(year === null ? { data: 'Scegli una data tra il 2000 e il 2200.' } : {}),
        ...(missingContract ? { contract: 'Scegli un contratto con un tetto.' } : {}),
      })
      setAsked(null)
      return
    }
    setErrors({})
    attempts.current += 1
    setAsked({ year: year, input: request.input, attempt: attempts.current })
  }

  return (
    <form onSubmit={submit} className="space-y-4 border-t pt-4" noValidate>
      <div>
        <h3 className="font-semibold">Ci sta?</h3>
        <p className="text-sm text-muted-foreground">
          {hasCeilings
            ? 'Aggiungi ai ricavi incassati dell’anno la stima di un lavoro e guarda se resti sotto le soglie. Non viene salvato niente.'
            : 'Aggiungi ai ricavi del cliente la stima di un lavoro e guarda se resta sotto il tetto del contratto. Non viene salvato niente.'}
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
            legalEntityId={legalEntityId}
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
              onChange={(event) => edit(setValue)(event.target.value)}
              aria-invalid={errors.value ? true : undefined}
            />
            {errors.value ? <FieldError>{errors.value}</FieldError> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${uid}-ore`}>oppure Ore</Label>
            <Input
              id={`${uid}-ore`}
              inputMode="decimal"
              placeholder="100"
              value={hours}
              onChange={(event) => edit(setHours)(event.target.value)}
              aria-invalid={errors.hours ? true : undefined}
            />
            {errors.hours ? <FieldError>{errors.hours}</FieldError> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${uid}-tariffa`}>per Tariffa oraria</Label>
            <Input
              id={`${uid}-tariffa`}
              inputMode="decimal"
              placeholder="80,00"
              value={rate}
              onChange={(event) => edit(setRate)(event.target.value)}
              aria-invalid={errors.rate ? true : undefined}
            />
            {errors.rate ? <FieldError>{errors.rate}</FieldError> : null}
          </div>
          {errors.form ? (
            <div className="sm:col-span-3">
              <FieldError>{errors.form}</FieldError>
            </div>
          ) : null}
        </div>
      )}

      {contracts.length > 0 ? (
        <div className="space-y-2">
          <Label htmlFor={`${uid}-contratto`}>Contratto con un tetto</Label>
          <ContractPicker
            id={`${uid}-contratto`}
            contracts={contracts}
            value={contractId}
            required={!hasCeilings}
            onSelect={edit(setContractId)}
          />
          {errors.contract ? <FieldError>{errors.contract}</FieldError> : null}
        </div>
      ) : null}

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
          <p className="text-xs text-muted-foreground">
            {hasCeilings
              ? 'Conta l’anno solare dell’incasso.'
              : 'Conta l’anno di anniversario del contratto che contiene questa data.'}
          </p>
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
