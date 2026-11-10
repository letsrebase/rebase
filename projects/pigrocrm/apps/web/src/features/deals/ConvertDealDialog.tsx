import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { LoadingState } from '@/components/LoadingState'
import {
  CADENCES,
  EXPENSE_POLICY_KINDS,
  EXPENSE_POLICY_LABELS,
  RATE_TYPE_OPTIONS,
  RENEWAL_LABELS,
  RENEWAL_TYPES,
  RatePeriods,
  cadenceLabel,
  formatAmount,
  type ExpensePolicyKind,
  type RenewalType,
} from '@/features/contracts/labels'
import type { ContractFormValues } from '@/features/contracts/contractFormValues'
import {
  proposalToFormValues,
  toConvertBody,
  useContractProposal,
  useConvertDeal,
  type ContractProposal,
} from '@/features/deals/convertDeal'
import { DynamicForm } from '@/components/DynamicForm'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useEntitySchema } from '@/lib/schema'

interface Props {
  dealId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** The API fields this dialog has a control for; an error on any other is shown above the
 *  buttons instead of vanishing. */
const DRAWN_FIELDS = [
  'title',
  'start',
  'end',
  'renewal_type',
  'renewal_notice_days',
  'termination_notice_days',
  'billing_cadence',
  'expense_policy',
]

/**
 * «Crea contratto»: the contract a won deal becomes (REB-934). The deal gives the customer
 * and the rate cards, and proposes the title, the start and the end; the person confirms
 * those and says what only they know, the renewal, the notices, the billing cadence and the
 * expense policy. Payment terms are not asked: they stay empty and cascade from the customer.
 * On success the page moves to the new contract.
 *
 * The proposal is read when the dialog opens, and the form below it mounts only once it
 * has arrived, so its first state is the proposal and never an empty form that a late answer
 * overwrites. A deal that cannot convert (not won, no lines, already a contract) answers a
 * refusal, printed instead of the form.
 */
export function ConvertDealDialog({ dealId, open, onOpenChange }: Props) {
  const proposal = useContractProposal(dealId, open)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Crea contratto</DialogTitle>
          <DialogDescription>
            Il deal vinto diventa un contratto in bozza, con una scheda tariffaria per ogni riga di
            valore.
          </DialogDescription>
        </DialogHeader>
        {proposal.isLoading && <LoadingState />}
        {proposal.isError && (
          <p className="text-sm text-destructive">{toProblem(proposal.error).detail}</p>
        )}
        {proposal.data && (
          <ConvertForm dealId={dealId} initial={proposal.data} onClose={() => onOpenChange(false)} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function CardsPreview({ cards }: { cards: ContractProposal['cards'] }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Schede tariffarie
      </h3>
      <ul className="divide-y rounded-md border text-sm">
        {cards.map((card, index) => (
          <li key={index} className="flex justify-between gap-4 px-3 py-2">
            <span>
              {RATE_TYPE_OPTIONS[card.type]}
              {card.delivery_period != null && card.type !== 'one_off'
                ? ` · ${RatePeriods[card.delivery_period]}`
                : ''}
              {card.expected_quantity != null ? ` · ${card.expected_quantity} previsti` : ''}
            </span>
            <span className="text-right font-medium">{formatAmount(card.amount, 'EUR')}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

function ConvertForm({
  dealId,
  initial,
  onClose,
}: {
  dealId: string
  initial: ContractProposal
  onClose: () => void
}) {
  const navigate = useNavigate()
  const convert = useConvertDeal(dealId)
  const schema = useEntitySchema('contract')
  const customFields = schema.data?.custom_fields ?? []
  const isRenderedCustomKey = (key: string) => customFields.some((field) => field.key === key)
  const [values, setValues] = useState<ContractFormValues>(() => proposalToFormValues(initial))
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  // The lines with no start of their own follow the contract's, so moving the start moves the
  // end and the cards the deal proposes: they are asked again for the new start, never
  // worked out here. Until the answer arrives the form keeps the previous one.
  const movedStart =
    ISO_DATE.test(values.start) && values.start !== initial.start ? values.start : undefined
  const refreshed = useContractProposal(dealId, movedStart !== undefined, movedStart)
  const proposal = movedStart === undefined ? initial : (refreshed.data ?? initial)
  // The end follows the answer, whenever a new one is shown, and is the person's own until then.
  const [shown, setShown] = useState(proposal)
  if (proposal !== shown) {
    setShown(proposal)
    setValues((previous) => ({ ...previous, end: proposal.end ?? '' }))
  }
  const refreshing = movedStart !== undefined && refreshed.isFetching

  function set<K extends keyof ContractFormValues>(key: K, value: ContractFormValues[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  function submit() {
    setProblem(null)
    convert.mutate(toConvertBody(values, isRenderedCustomKey), {
      onSuccess: (contract) => {
        onClose()
        toast.success('Contratto creato')
        void navigate({ to: '/app/contracts/$contractId', params: { contractId: contract.id } })
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  const drawn =
    fieldError !== null &&
    (DRAWN_FIELDS.includes(fieldError.field) || isRenderedCustomKey(fieldError.field))
  const unattributed =
    problem === null || drawn
      ? null
      : fieldError === null
        ? problem.detail
        : `${fieldError.field}: ${fieldError.message}`

  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="convert-title">Titolo</Label>
        <Input
          id="convert-title"
          value={values.title}
          onChange={(event) => set('title', event.target.value)}
          aria-invalid={fieldError?.field === 'title'}
        />
        {errorOn('title')}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="convert-start">Inizio</Label>
          <Input
            id="convert-start"
            type="date"
            value={values.start}
            onChange={(event) => set('start', event.target.value)}
            aria-invalid={fieldError?.field === 'start'}
          />
          <p className="text-xs text-muted-foreground">
            Le righe senza una data propria partono da qui.
          </p>
          {errorOn('start')}
          {refreshed.isError && movedStart !== undefined && (
            <p className="text-sm text-destructive">{toProblem(refreshed.error).detail}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="convert-end">Fine</Label>
          <Input
            id="convert-end"
            type="date"
            value={values.end}
            onChange={(event) => set('end', event.target.value)}
            aria-invalid={fieldError?.field === 'end'}
          />
          <p className="text-xs text-muted-foreground">
            {proposal.end === null
              ? 'Nessuna riga indica una fine: lasciala vuota per un contratto senza scadenza, o scrivi la data di consegna.'
              : 'L’ultimo giorno coperto dalle righe del deal.'}
          </p>
          {errorOn('end')}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="convert-renewal">Rinnovo</Label>
          <Select
            value={values.renewal_type}
            onValueChange={(value) => set('renewal_type', value as RenewalType)}
          >
            <SelectTrigger id="convert-renewal" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RENEWAL_TYPES.map((type) => (
                <SelectItem key={type} value={type}>
                  {RENEWAL_LABELS[type]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errorOn('renewal_type')}
        </div>
        {values.renewal_type !== 'none' && (
          <div className="space-y-2">
            <Label htmlFor="convert-notice-renewal">Preavviso di rinnovo (giorni)</Label>
            <Input
              id="convert-notice-renewal"
              type="number"
              min={0}
              value={values.renewal_notice_days}
              onChange={(event) => set('renewal_notice_days', event.target.value)}
              aria-invalid={fieldError?.field === 'renewal_notice_days'}
            />
            {errorOn('renewal_notice_days')}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="convert-notice-termination">Preavviso di disdetta (giorni)</Label>
          <Input
            id="convert-notice-termination"
            type="number"
            min={0}
            value={values.termination_notice_days}
            onChange={(event) => set('termination_notice_days', event.target.value)}
            aria-invalid={fieldError?.field === 'termination_notice_days'}
          />
          {errorOn('termination_notice_days')}
        </div>
        <div className="space-y-2">
          <Label htmlFor="convert-cadence">Cadenza di fatturazione</Label>
          <Select
            value={values.billing_cadence}
            onValueChange={(value) => set('billing_cadence', value)}
          >
            <SelectTrigger id="convert-cadence" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CADENCES.map((cadence) => (
                <SelectItem key={cadence} value={cadence}>
                  {cadenceLabel(cadence)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errorOn('billing_cadence')}
        </div>
      </div>

      <div className="space-y-3">
        <div className="space-y-2">
          <Label htmlFor="convert-expenses">Politica sulle spese</Label>
          <Select
            value={values.expenses_kind}
            onValueChange={(value) => set('expenses_kind', value as ExpensePolicyKind)}
          >
            <SelectTrigger id="convert-expenses" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EXPENSE_POLICY_KINDS.map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {EXPENSE_POLICY_LABELS[kind]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errorOn('expense_policy')}
        </div>
        {values.expenses_kind === 'reimbursable_with_cap' && (
          <div className="space-y-2">
            <Label htmlFor="convert-expenses-cap">Tetto di rimborso</Label>
            <Input
              id="convert-expenses-cap"
              inputMode="decimal"
              placeholder="500.00"
              value={values.expenses_cap}
              onChange={(event) => set('expenses_cap', event.target.value)}
            />
          </div>
        )}
        {values.expenses_kind !== 'not_reimbursable' && (
          <div className="flex items-center gap-2">
            <Checkbox
              id="convert-expenses-preauthorization"
              checked={values.expenses_preauthorization}
              onCheckedChange={(checked) => set('expenses_preauthorization', checked === true)}
            />
            <Label htmlFor="convert-expenses-preauthorization" className="font-normal">
              Ogni spesa va autorizzata prima
            </Label>
          </div>
        )}
      </div>

      {customFields.length > 0 && (
        <DynamicForm
          fields={customFields}
          values={values.custom}
          onChange={(key, value) => set('custom', { ...values.custom, [key]: value })}
          problem={fieldError !== null && isRenderedCustomKey(fieldError.field) ? problem : null}
          mode="create"
        />
      )}

      <CardsPreview cards={proposal.cards} />
      <p className="text-xs text-muted-foreground">
        I termini di pagamento sono quelli del cliente. Dopo la conversione le righe di valore del
        deal non si modificano più: il prezzo è quello del contratto.
      </p>

      {unattributed !== null && <p className="text-sm text-destructive">{unattributed}</p>}

      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>
          Annulla
        </Button>
        <Button onClick={submit} disabled={convert.isPending || refreshing}>
          {convert.isPending ? 'Creazione…' : 'Crea contratto'}
        </Button>
      </DialogFooter>
    </>
  )
}
