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
import { RATE_PERIODI, RATE_TIPI, RATE_UNITA, currencySign, formatAmount } from './labels'
import {
  useCreateRateCard,
  useRateCards,
  useUpdateRateCard,
  type RateCard,
  type RateCardCreateBody,
  type RateCardUpdateBody,
} from './queries'

type Tipo = RateCardCreateBody['tipo']
type Unita = RateCardCreateBody['unita']
type Periodo = NonNullable<RateCardCreateBody['periodo_erogazione']>

const TIPI = Object.keys(RATE_TIPI) as Tipo[]
const UNITA = Object.keys(RATE_UNITA) as Unita[]
const PERIODI = Object.keys(RATE_PERIODI) as Periodo[]

interface RateFormValues {
  valido_da: string
  valido_a: string
  tipo: Tipo
  importo: string
  unita: Unita
  frazioni: string
  ore_minime: string
  periodo_erogazione: Periodo | ''
}

const EMPTY_FORM: RateFormValues = {
  valido_da: '',
  valido_a: '',
  tipo: 'giornaliero',
  importo: '',
  unita: 'giorno',
  frazioni: '1',
  ore_minime: '',
  periodo_erogazione: '',
}

function toFormValues(card: RateCard): RateFormValues {
  return {
    valido_da: card.valido_da,
    valido_a: card.valido_a ?? '',
    tipo: card.tipo as Tipo,
    importo: String(card.importo),
    unita: card.unita as Unita,
    frazioni: card.frazioni_ammesse.map(String).join(', '),
    ore_minime: card.ore_minime === null ? '' : String(card.ore_minime),
    periodo_erogazione: (card.periodo_erogazione ?? '') as Periodo | '',
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
    valido_da: values.valido_da,
    valido_a: values.valido_a === '' ? null : values.valido_a,
    tipo: values.tipo,
    importo: values.importo.trim().replace(',', '.'),
    unita: values.unita,
    frazioni_ammesse: parseFractions(values.frazioni),
    ore_minime: values.tipo === 'orario' && values.ore_minime.trim() !== ''
      ? values.ore_minime.trim().replace(',', '.')
      : null,
    periodo_erogazione:
      values.tipo === 'ricorrente_fisso' && values.periodo_erogazione !== ''
        ? values.periodo_erogazione
        : null,
  }
}

/** Where a card stands today, read from its own two dates: never stored, so a card
 *  closed by a newer one and a card that has not started yet read right without a job. */
function standing(card: RateCard, today: string): 'futura' | 'in_vigore' | 'chiusa' {
  if (card.valido_da > today) return 'futura'
  if (card.valido_a !== null && card.valido_a < today) return 'chiusa'
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
      card.valido_da < start &&
      (card.valido_a === null || card.valido_a >= start),
  )
}

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contractId: string
  divisa: string
  cards: RateCard[]
  /** The card being edited, or absent for a new one. */
  card?: RateCard
}

function RateCardDialog({ open, onOpenChange, contractId, divisa, cards, card }: DialogProps) {
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

  const replaced = isCreate ? cardInForceOn(cards, values.valido_da) : undefined

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
          chiudiPrecedente: replaced !== undefined && closePrevious,
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
      'valido_da',
      'valido_a',
      'tipo',
      'unita',
      'importo',
      'frazioni_ammesse',
      'ore_minime',
      'periodo_erogazione',
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
              value={values.valido_da}
              onChange={(event) => set('valido_da', event.target.value)}
              aria-invalid={fieldError?.field === 'valido_da' || overlap !== null}
            />
            {errorOn('valido_da')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="rate-a">Valida fino al</Label>
            <Input
              id="rate-a"
              type="date"
              value={values.valido_a}
              onChange={(event) => set('valido_a', event.target.value)}
              aria-invalid={fieldError?.field === 'valido_a' || overlap !== null}
            />
            <p className="text-xs text-muted-foreground">Vuoto: la tariffa resta in vigore.</p>
            {errorOn('valido_a')}
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
              Chiudi la tariffa in vigore dal {formatDate(replaced.valido_da)} il giorno prima
              di questa
            </Label>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="rate-tipo">Tipo</Label>
            <Select value={values.tipo} onValueChange={(value) => set('tipo', value as Tipo)}>
              <SelectTrigger id="rate-tipo" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIPI.map((tipo) => (
                  <SelectItem key={tipo} value={tipo}>
                    {RATE_TIPI[tipo]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('tipo')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="rate-unita">Unità</Label>
            <Select value={values.unita} onValueChange={(value) => set('unita', value as Unita)}>
              <SelectTrigger id="rate-unita" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {UNITA.map((unita) => (
                  <SelectItem key={unita} value={unita}>
                    {RATE_UNITA[unita]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('unita')}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="rate-importo">Importo ({currencySign(divisa)})</Label>
            <Input
              id="rate-importo"
              inputMode="decimal"
              placeholder="400.00"
              value={values.importo}
              onChange={(event) => set('importo', event.target.value)}
              aria-invalid={fieldError?.field === 'importo'}
            />
            {errorOn('importo')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="rate-frazioni">Frazioni ammesse</Label>
            <Input
              id="rate-frazioni"
              value={values.frazioni}
              onChange={(event) => set('frazioni', event.target.value)}
              aria-invalid={fieldError?.field === 'frazioni_ammesse'}
            />
            <p className="text-xs text-muted-foreground">Per esempio «1, 0,5» per giorno intero o mezzo.</p>
            {errorOn('frazioni_ammesse')}
          </div>
        </div>

        {values.tipo === 'orario' && (
          <div className="space-y-2">
            <Label htmlFor="rate-ore-minime">Ore minime</Label>
            <Input
              id="rate-ore-minime"
              inputMode="decimal"
              value={values.ore_minime}
              onChange={(event) => set('ore_minime', event.target.value)}
              aria-invalid={fieldError?.field === 'ore_minime'}
            />
            {errorOn('ore_minime')}
          </div>
        )}
        {values.tipo === 'ricorrente_fisso' && (
          <div className="space-y-2">
            <Label htmlFor="rate-periodo">Erogazione del canone</Label>
            <Select
              value={values.periodo_erogazione}
              onValueChange={(value) => set('periodo_erogazione', value as Periodo)}
            >
              <SelectTrigger id="rate-periodo" className="w-full">
                <SelectValue placeholder="Seleziona…" />
              </SelectTrigger>
              <SelectContent>
                {PERIODI.map((periodo) => (
                  <SelectItem key={periodo} value={periodo}>
                    {RATE_PERIODI[periodo]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Senza una cadenza il canone non entra nei ricavi programmati.
            </p>
            {errorOn('periodo_erogazione')}
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
  return card.valido_a === null
    ? `dal ${formatDate(card.valido_da)}`
    : `dal ${formatDate(card.valido_da)} al ${formatDate(card.valido_a)}`
}

function details(card: RateCard): string {
  const parts: string[] = []
  if (card.ore_minime !== null) parts.push(`minimo ${formatQuantity(String(card.ore_minime))} ore`)
  if (card.periodo_erogazione !== null) {
    parts.push(
      card.periodo_erogazione in RATE_PERIODI
        ? RATE_PERIODI[card.periodo_erogazione as Periodo].toLowerCase()
        : card.periodo_erogazione,
    )
  }
  const fractions = card.frazioni_ammesse.map((fraction) => formatQuantity(String(fraction)))
  if (fractions.join() !== '1') parts.push(`frazioni ${fractions.join(' · ')}`)
  return parts.join(' · ')
}

const STANDING_PILL = {
  futura: { tone: 'gold', label: 'Futura' },
  in_vigore: { tone: 'ink', label: 'In vigore' },
  chiusa: { tone: 'muted', label: 'Chiusa' },
} as const

/**
 * The «Tariffe» section of a contract's page (REB-742): the rate cards by validity,
 * oldest term first, with the one in force marked. A card says where it stands from its
 * two dates, so a rate added from January leaves the old one reading «Chiusa» with the
 * day before as its last day. Overlaps are the database's refusal, shown here as the
 * sentence the API gives; «chiudi il giorno prima» is the one way to replace a rate
 * without editing the old card by hand.
 */
export function RateCardsSection({ contractId, divisa }: { contractId: string; divisa: string }) {
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
                <li key={card.id} className="flex items-center gap-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium">{validity(card)}</span>
                      <StatusPill tone={pill.tone}>{pill.label}</StatusPill>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {RATE_TIPI[card.tipo as Tipo] ?? card.tipo}
                      {extra === '' ? '' : ` · ${extra}`}
                    </p>
                  </div>
                  <span className="shrink-0 tabular-nums">
                    {formatAmount(String(card.importo), divisa)} / {RATE_UNITA[card.unita as Unita] ?? card.unita}
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
        divisa={divisa}
        cards={cards.data ?? []}
        card={dialog.card}
      />
    </Card>
  )
}
