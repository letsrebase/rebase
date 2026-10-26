import { Link, useNavigate } from '@tanstack/react-router'
import { CalendarPlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { money } from '@/features/dashboard/format'
import { toProblem } from '@/lib/api'
import { toIsoDate, formatIsoDateItalian } from '@/lib/dates'
import { useCanWrite } from '@/lib/auth'
import { cn } from '@rebase/ui/cn'
import {
  FULL_DAY,
  HALF_DAY,
  activeRateCard,
  allowsFraction,
  quantitaLabel,
  statoLabel,
  unitaLabel,
} from './labels'
import { useDayContracts, useDayRateCards, useRecordDay, useWorkUnits } from './queries'

/** The states that leave a date free again (`uq_work_units_contract_data_live`). */
const FREES_THE_DATE = new Set(['revocato', 'rifiutato'])

/**
 * `/app/days/new`: record a day worked against a contract, made to be used on a phone.
 *
 * Four things to say (which contract, which date, a whole day or half, what was done) and
 * the two facts that decide what happens next shown beside them before saving: the rate
 * in force on that date, and whether the contract wants an approval first. A day is
 * recorded as worked (`lavorato`); on a contract that requires prior approval and has none
 * linked the ledger itself flags it `lavorato_senza_approvazione`, so this form warns and
 * never blocks, the way mastro's did. The one thing it does block is a second day on a date
 * the contract already holds one for: the database allows one live day per contract per
 * date and would answer 409.
 *
 * The rate is displayed as the string the API sent. Nothing here multiplies it by the
 * quantity: what a day is worth is the invoice's figure, computed server-side.
 */
export function DayForm({
  initialContractId,
  initialDate,
}: {
  initialContractId?: string
  initialDate?: string
}) {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const contracts = useDayContracts()
  const record = useRecordDay()
  const [contractId, setContractId] = useState(initialContractId ?? '')
  const [data, setData] = useState(initialDate ?? toIsoDate(new Date()))
  const [quantita, setQuantita] = useState(FULL_DAY)
  const [descrizione, setDescrizione] = useState('')

  // The only contract on offer is the one to record against: picking it from a list of one
  // is a tap nobody needs. Derived at render, not an effect, so the field never shows
  // empty for a frame.
  const options = contracts.data ?? []
  const effectiveContractId = contractId !== '' ? contractId : options.length === 1 ? (options[0]?.id ?? '') : ''
  const contract = options.find((candidate) => candidate.id === effectiveContractId)

  const rateCards = useDayRateCards(effectiveContractId)
  const card = activeRateCard(rateCards.data ?? [], data)
  // Half a day on a contract whose rate allows only whole ones is a day the invoice cannot
  // price: `frazioni_ammesse` is what the rate card says it accepts.
  const halfAllowed = allowsFraction(card, HALF_DAY)
  const effectiveQuantita = quantita === HALF_DAY && !halfAllowed ? FULL_DAY : quantita

  const sameDate = useWorkUnits(
    { contractId: effectiveContractId, da: data, a: data },
    { enabled: effectiveContractId !== '' && data !== '' },
  )
  const taken = (sameDate.data?.items ?? []).find((day) => !FREES_THE_DATE.has(day.stato))

  if (!canWrite) {
    return (
      <>
        <PageHeader icon={CalendarPlus} title="Registra una giornata" />
        <p className="px-8 py-6 text-sm text-muted-foreground">
          Con il tuo ruolo puoi leggere le giornate ma non registrarne.
        </p>
      </>
    )
  }

  const outsideTerm =
    contract !== undefined && data !== '' && (data < contract.inizio || (contract.fine !== null && data > contract.fine))
  const ready = effectiveContractId !== '' && data !== '' && descrizione.trim() !== '' && taken === undefined

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!ready) return
    record.mutate(
      {
        contract_id: effectiveContractId,
        data,
        quantita: effectiveQuantita,
        descrizione: descrizione.trim(),
        stato: 'lavorato',
      },
      {
        onSuccess: (day) => {
          toast.success('Giornata registrata')
          void navigate({ to: '/app/days/$workUnitId', params: { workUnitId: day.id } })
        },
      },
    )
  }

  return (
    <>
      <PageHeader
        icon={CalendarPlus}
        title="Registra una giornata"
        description="Una giornata lavorata su un contratto: intera o mezza, con quello che hai fatto."
      />
      <form onSubmit={submit} className="max-w-xl space-y-6 px-4 py-6 sm:px-8" noValidate>
        {contracts.isError ? <QueryErrorBanner error={contracts.error} /> : null}

        <div className="space-y-2">
          <Label htmlFor="day-contract">Contratto</Label>
          <Select value={effectiveContractId} onValueChange={setContractId} disabled={contracts.isPending}>
            <SelectTrigger id="day-contract" className="w-full">
              <SelectValue placeholder={contracts.isPending ? 'Caricamento…' : 'Scegli il contratto'} />
            </SelectTrigger>
            <SelectContent>
              {options.map((candidate) => (
                <SelectItem key={candidate.id} value={candidate.id}>
                  {candidate.titolo}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {contracts.isSuccess && options.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nessun contratto attivo: una giornata si registra su un contratto.
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="day-date">Data</Label>
          <Input id="day-date" type="date" value={data} onChange={(event) => setData(event.target.value)} required />
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Quanto</legend>
          <div className="grid grid-cols-2 gap-2">
            <Choice
              name="day-quantity"
              value={FULL_DAY}
              checked={effectiveQuantita === FULL_DAY}
              onSelect={setQuantita}
              label="Giornata intera"
            />
            <Choice
              name="day-quantity"
              value={HALF_DAY}
              checked={effectiveQuantita === HALF_DAY}
              onSelect={setQuantita}
              label="Mezza giornata"
              disabled={!halfAllowed}
            />
          </div>
          {!halfAllowed ? (
            <p className="text-sm text-muted-foreground">
              La tariffa in vigore a questa data accetta solo giornate intere.
            </p>
          ) : null}
        </fieldset>

        <div className="space-y-2">
          <Label htmlFor="day-task">Cosa hai fatto</Label>
          <Input
            id="day-task"
            value={descrizione}
            onChange={(event) => setDescrizione(event.target.value)}
            placeholder="Per esempio: revisione architettura"
            required
          />
        </div>

        <RateLine contractChosen={effectiveContractId !== ''} loading={rateCards.isPending && effectiveContractId !== ''} card={card} data={data} />

        {contract?.requires_prior_approval ? (
          <Notice>
            Questo contratto richiede l’approvazione preventiva del cliente. La giornata si registra
            come «{statoLabel('lavorato_senza_approvazione')}» finché non ne colleghi una.
          </Notice>
        ) : null}
        {outsideTerm && contract ? (
          <Notice>
            La data è fuori dal contratto, che va dal {formatIsoDateItalian(contract.inizio)}
            {contract.fine ? ` al ${formatIsoDateItalian(contract.fine)}` : ''}.
          </Notice>
        ) : null}
        {taken ? (
          <Notice>
            Questo contratto ha già una giornata il {formatIsoDateItalian(taken.data)} ({quantitaLabel(taken.quantita).toLowerCase()},{' '}
            {statoLabel(taken.stato).toLowerCase()}).{' '}
            <Link to="/app/days/$workUnitId" params={{ workUnitId: taken.id }} className="underline underline-offset-4">
              Aprila
            </Link>
            : una data ha una sola giornata, e per registrarne un’altra va revocata o rifiutata.
          </Notice>
        ) : null}

        {record.isError ? (
          <p role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {toProblem(record.error).detail}
          </p>
        ) : null}

        <Button type="submit" size="lg" className="w-full sm:w-auto" disabled={!ready || record.isPending}>
          {record.isPending ? <Loader className="mr-2 size-4" /> : null}
          Registra giornata
        </Button>
      </form>
    </>
  )
}

function RateLine({
  contractChosen,
  loading,
  card,
  data,
}: {
  contractChosen: boolean
  loading: boolean
  card: ReturnType<typeof activeRateCard>
  data: string
}) {
  if (!contractChosen) return null
  if (loading) return <p className="text-sm text-muted-foreground">Cerco la tariffa…</p>
  if (card === null) {
    return (
      <p className="text-sm text-muted-foreground">
        Nessuna tariffa in vigore il {formatIsoDateItalian(data)}: la giornata si registra e si prezza quando ce n’è una.
      </p>
    )
  }
  return (
    <p className="text-sm">
      <span className="text-muted-foreground">Tariffa in vigore: </span>
      <strong className="font-semibold tabular-nums">{money(card.importo)}</strong>{' '}
      <span className="text-muted-foreground">al {unitaLabel(card.unita)}</span>
    </p>
  )
}

function Notice({ children }: { children: React.ReactNode }) {
  return <p className="border border-muted bg-muted/40 px-3 py-2 text-sm">{children}</p>
}

/**
 * One option of a pair, a native radio under a label sized for a thumb. The control is the
 * input, so the keyboard, the screen reader and the form all behave as a radio group does;
 * the label only draws it.
 */
function Choice({
  name,
  value,
  checked,
  onSelect,
  label,
  disabled = false,
}: {
  name: string
  value: string
  checked: boolean
  onSelect: (value: string) => void
  label: string
  disabled?: boolean
}) {
  return (
    <label
      className={cn(
        'flex min-h-12 cursor-pointer items-center justify-center border px-3 text-sm font-medium transition-colors',
        'has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50',
        checked ? 'border-primary bg-primary text-primary-foreground' : 'bg-background hover:bg-muted/50',
        disabled && 'cursor-not-allowed opacity-50 hover:bg-background',
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={() => onSelect(value)}
        className="sr-only"
      />
      {label}
    </label>
  )
}
