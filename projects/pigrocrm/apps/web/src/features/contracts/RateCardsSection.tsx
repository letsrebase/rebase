import { Pencil, Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Skeleton } from '@rebase/ui/skeleton'
import { formatDate, formatQuantity } from '@/features/invoices/format'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { RatePeriods, RATE_TIPI, RateUnit, currencySign, formatAmount } from './labels'
import {
  useCreateRateCard,
  useRateCards,
  useUpdateRateCard,
  type RateCard,
  type RateCardCreateBody,
  type RateCardUpdateBody,
} from './queries'

type Type = RateCardCreateBody['type']
type Unit = RateCardCreateBody['unit']
type Period = NonNullable<RateCardCreateBody['delivery_period']>

const TIPI = Object.keys(RATE_TIPI) as Type[]
const Unit = Object.keys(RateUnit) as Unit[]
const Periods = Object.keys(RatePeriods) as Period[]

interface RateFormValues {
  valid_from: string
  valid_to: string
  type: Type
  amount: string
  unit: Unit
  fractions: string
  minimum_hours: string
  delivery_period: Period | ''
}

const EMPTY_FORM: RateFormValues = {
  valid_from: '',
  valid_to: '',
  type: 'daily',
  amount: '',
  unit: 'day',
  fractions: '1',
  minimum_hours: '',
  delivery_period: '',
}

function toFormValues(card: RateCard): RateFormValues {
  return {
    valid_from: card.valid_from,
    valid_to: card.valid_to ?? '',
    type: card.type as Type,
    amount: String(card.amount),
    unit: card.unit as Unit,
    fractions: card.allowed_fractions.map(String).join(', '),
    minimum_hours: card.minimum_hours === null ? '' : String(card.minimum_hours),
    delivery_period: (card.delivery_period ?? '') as Period | '',
  }
}

/** «1, 0,5» as the list of decimal strings the API stores. A comma between numbers is
 *  the separator, so a decimal comma is read as a point first: «0,5» is one half. */
function parseFractions(text: string): string[] {
  return text
    .split(/[;\s]+|,(?=\s)/)
    .map((part) => part.trim().replace(',', '.'))
    .filter((part) => part !== '')
}

function toBody(values: RateFormValues): RateCardUpdateBody {
  return {
    valid_from: values.valid_from,
    valid_to: values.valid_to === '' ? null : values.valid_to,
    type: values.type,
    amount: values.amount.trim().replace(',', '.'),
    unit: values.unit,
    allowed_fractions: parseFractions(values.fractions),
    minimum_hours: values.type === 'hourly' && values.minimum_hours.trim() !== ''
      ? values.minimum_hours.trim().replace(',', '.')
      : null,
    delivery_period:
      values.type === 'fixed_recurring' && values.delivery_period !== ''
        ? values.delivery_period
        : null,
  }
}

/** Where a card stands today, read from its own two dates: never stored, so a card
 *  closed by a newer one and a card that has not started yet read right without a job. */
function standing(card: RateCard, today: string): 'future' | 'in_vigore' | 'closed' {
  if (card.valid_from > today) return 'future'
  if (card.valid_to !== null && card.valid_to < today) return 'closed'
  return 'in_vigore'
}

/**
 * The card in force on `start`, when one began earlier: the one a new rate from `start`
 * would overlap, and the one «chiudi il giorno prima» ends. A hint to the person only;
 * the server decides what it closes and the database what it refuses.
 */
function cardInForceOn(cards: RateCard[], start: string, except?: string): RateCard | undefined {
  if (start === '') return undefined
  return cards.find(
    (card) =>
      card.id !== except &&
      card.valid_from < start &&
      (card.valid_to === null || card.valid_to >= start),
  )
}

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contractId: string
  currency: string
  cards: RateCard[]
  /** The card being edited, or absent for a new one. */
  card?: RateCard
}

function RateCardDialog({ open, onOpenChange, contractId, currency, cards, card }: DialogProps) {
  const isCreate = card === undefined
  const initial = card === undefined ? EMPTY_FORM : toFormValues(card)
  const [values, setValues] = useState<RateFormValues>(initial)
  const [closePrevious, setClosePrevious] = useState(true)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const create = useCreateRateCard(contractId)
  const update = useUpdateRateCard(contractId, card?.id ?? '')

  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(initial)
      setClosePrevious(true)
      setProblem(null)
    }
  }

  function set<K extends keyof RateFormValues>(key: K, value: RateFormValues[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  const replaced = isCreate ? cardInForceOn(cards, values.valid_from) : undefined

  function submit() {
    const body = toBody(values)
    setProblem(null)
    const done = (message: string) => () => {
      onOpenChange(false)
      toast.success(message)
    }
    if (isCreate) {
      create.mutate(
        {
          body: body as RateCardCreateBody,
          closePrevious: replaced !== undefined && closePrevious,
        },
        { onSuccess: done('Tariffa aggiunta'), onError: (error) => setProblem(toProblem(error)) },
      )
      return
    }
    update.mutate(body, {
      onSuccess: done('Tariffa aggiornata'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const busy = create.isPending || update.isPending
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  // An overlap is a 409 with no field: it is the answer to the dates, so it is read
  // under them and not as a banner nobody connects to the two inputs.
  const overlap = problem?.status === 409 ? problem.detail : null
  // Whatever no control above answers for (a model-level error on `body`, a bad item of
  // the fractions list) still has to be read, or saving would seem to do nothing.
  const drawn =
    fieldError !== null &&
    [
      'valid_from',
      'valid_to',
      'type',
      'unit',
      'amount',
      'allowed_fractions',
      'minimum_hours',
      'delivery_period',
    ].includes(fieldError.field)
  const elsewhere =
    problem !== null && overlap === null && !drawn
      ? fieldError === null
        ? problem.detail
        : fieldError.message
      : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isCreate ? 'Nuova tariffa' : 'Modifica tariffa'}</DialogTitle>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="rate-da">Valida dal</Label>
            <Input
              id="rate-da"
              type="date"
              value={values.valid_from}
              onChange={(event) => set('valid_from', event.target.value)}
              aria-invalid={fieldError?.field === 'valid_from' || overlap !== null}
            />
            {errorOn('valid_from')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="rate-a">Valida fino al</Label>
            <Input
              id="rate-a"
              type="date"
              value={values.valid_to}
              onChange={(event) => set('valid_to', event.target.value)}
              aria-invalid={fieldError?.field === 'valid_to' || overlap !== null}
            />
            <p className="text-xs text-muted-foreground">Vuoto: la tariffa resta in vigore.</p>
            {errorOn('valid_to')}
          </div>
        </div>
        {overlap !== null && <p className="text-sm text-destructive">{overlap}</p>}

        {replaced !== undefined && (
          <div className="flex items-start gap-2 rounded-lg border p-3">
            <Checkbox
              id="rate-close-previous"
              checked={closePrevious}
              onCheckedChange={(checked) => setClosePrevious(checked === true)}
            />
            <Label htmlFor="rate-close-previous" className="font-normal leading-snug">
              Chiudi la tariffa in vigore dal {formatDate(replaced.valid_from)} il giorno prima
              di questa
            </Label>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="rate-tipo">Tipo</Label>
            <Select value={values.type} onValueChange={(value) => set('type', value as Type)}>
              <SelectTrigger id="rate-tipo" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIPI.map((type) => (
                  <SelectItem key={type} value={type}>
                    {RATE_TIPI[type]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('type')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="rate-unita">Unità</Label>
            <Select value={values.unit} onValueChange={(value) => set('unit', value as Unit)}>
              <SelectTrigger id="rate-unita" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Unit.map((unit) => (
                  <SelectItem key={unit} value={unit}>
                    {RateUnit[unit]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('unit')}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="rate-importo">Importo ({currencySign(currency)})</Label>
            <Input
              id="rate-importo"
              inputMode="decimal"
              placeholder="400.00"
              value={values.amount}
              onChange={(event) => set('amount', event.target.value)}
              aria-invalid={fieldError?.field === 'amount'}
            />
            {errorOn('amount')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="rate-frazioni">Frazioni ammesse</Label>
            <Input
              id="rate-frazioni"
              value={values.fractions}
              onChange={(event) => set('fractions', event.target.value)}
              aria-invalid={fieldError?.field === 'allowed_fractions'}
            />
            <p className="text-xs text-muted-foreground">Per esempio «1, 0,5» per giorno intero o mezzo.</p>
            {errorOn('allowed_fractions')}
          </div>
        </div>

        {values.type === 'hourly' && (
          <div className="space-y-2">
            <Label htmlFor="rate-ore-minime">Ore minime</Label>
            <Input
              id="rate-ore-minime"
              inputMode="decimal"
              value={values.minimum_hours}
              onChange={(event) => set('minimum_hours', event.target.value)}
              aria-invalid={fieldError?.field === 'minimum_hours'}
            />
            {errorOn('minimum_hours')}
          </div>
        )}
        {values.type === 'fixed_recurring' && (
          <div className="space-y-2">
            <Label htmlFor="rate-periodo">Erogazione del canone</Label>
            <Select
              value={values.delivery_period}
              onValueChange={(value) => set('delivery_period', value as Period)}
            >
              <SelectTrigger id="rate-periodo" className="w-full">
                <SelectValue placeholder="Seleziona…" />
              </SelectTrigger>
              <SelectContent>
                {Periods.map((period) => (
                  <SelectItem key={period} value={period}>
                    {RatePeriods[period]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Senza una cadenza il canone non entra nei ricavi programmati.
            </p>
            {errorOn('delivery_period')}
          </div>
        )}

        {elsewhere !== null && <p className="text-sm text-destructive">{elsewhere}</p>}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function validity(card: RateCard): string {
  return card.valid_to === null
    ? `dal ${formatDate(card.valid_from)}`
    : `dal ${formatDate(card.valid_from)} al ${formatDate(card.valid_to)}`
}

function details(card: RateCard): string {
  const parts: string[] = []
  if (card.minimum_hours !== null) parts.push(`minimo ${formatQuantity(String(card.minimum_hours))} ore`)
  if (card.delivery_period !== null) {
    parts.push(
      card.delivery_period in RatePeriods
        ? RatePeriods[card.delivery_period as Period].toLowerCase()
        : card.delivery_period,
    )
  }
  const fractions = card.allowed_fractions.map((fraction) => formatQuantity(String(fraction)))
  if (fractions.join() !== '1') parts.push(`frazioni ${fractions.join(' · ')}`)
  return parts.join(' · ')
}

const STANDING_PILL = {
  future: { tone: 'gold', label: 'Futura' },
  in_vigore: { tone: 'ink', label: 'In vigore' },
  closed: { tone: 'muted', label: 'Chiusa' },
} as const

/**
 * The «Tariffe» section of a contract's page (REB-742): the rate cards by validity,
 * oldest term first, with the one in force marked. A card says where it stands from its
 * two dates, so a rate added from January leaves the old one reading «Chiusa» with the
 * day before as its last day. Overlaps are the database's refusal, shown here as the
 * sentence the API gives; «chiudi il giorno prima» is the one way to replace a rate
 * without editing the old card by hand.
 */
export function RateCardsSection({ contractId, currency }: { contractId: string; currency: string }) {
  const canWrite = useCan('create_rate_card')
  const cards = useRateCards(contractId)
  const [dialog, setDialog] = useState<{ open: boolean; card?: RateCard }>({ open: false })
  const today = toIsoDate(new Date())

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">Tariffe</h2>
          {canWrite && (
            <Button size="sm" variant="outline" onClick={() => setDialog({ open: true })}>
              <Plus className="mr-2 size-4" />
              Nuova tariffa
            </Button>
          )}
        </div>

        {cards.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : cards.isError ? (
          <QueryErrorBanner error={cards.error} />
        ) : (cards.data ?? []).length === 0 ? (
          <p className="text-muted-foreground">Nessuna tariffa: senza una il contratto non ha prezzi.</p>
        ) : (
          <ul className="divide-y">
            {(cards.data ?? []).map((card) => {
              const pill = STANDING_PILL[standing(card, today)]
              const extra = details(card)
              return (
                <li key={card.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                  {/* A text column no narrower than 11rem: below it the amount and the pencil
                      drop to a line of their own instead of squeezing the words over each other. */}
                  <div className="min-w-44 flex-1">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium">{validity(card)}</span>
                      <StatusPill tone={pill.tone}>{pill.label}</StatusPill>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {RATE_TIPI[card.type as Type] ?? card.type}
                      {extra === '' ? '' : ` · ${extra}`}
                    </p>
                  </div>
                  <span className="shrink-0 tabular-nums">
                    {formatAmount(String(card.amount), currency)} / {RateUnit[card.unit as Unit] ?? card.unit}
                  </span>
                  {canWrite && (
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`Modifica la tariffa ${validity(card)}`}
                      onClick={() => setDialog({ open: true, card })}
                    >
                      <Pencil className="size-4" />
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>

      <RateCardDialog
        open={dialog.open}
        onOpenChange={(open) => setDialog((previous) => ({ ...previous, open }))}
        contractId={contractId}
        currency={currency}
        cards={cards.data ?? []}
        card={dialog.card}
      />
    </Card>
  )
}
