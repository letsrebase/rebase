import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DynamicForm } from '@/components/DynamicForm'
import { Button } from '@rebase/ui/button'
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
import { Textarea } from '@rebase/ui/textarea'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useEntitySchema } from '@/lib/schema'
import {
  CADENCES,
  EXPENSE_POLICY_KINDS,
  EXPENSE_POLICY_LABELS,
  RENEWAL_LABELS,
  RENEWAL_TYPES,
  cadenceLabel,
  type ExpensePolicyKind,
  type RenewalType,
} from './labels'
import {
  contractToFormValues,
  defaultContractFormValues,
  toContractBody,
  type ContractFormValues,
} from './contractFormValues'
import {
  useCreateContract,
  useUpdateContract,
  type Contract,
  type ContractCreateBody,
} from './queries'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Set when composing a contract for this customer. Absent on edit. */
  customerId?: string
  /** Set when editing: the stored row, and so which mode this is. */
  contract?: Contract
  onSaved?: (contract: Contract) => void
}

const EMPTY_SELECT_HINT = 'Seleziona…'

/** The API fields this dialog has a control for, so an error on any other is shown
 *  above the buttons instead of vanishing. */
const DRAWN_FIELDS = [
  'title',
  'start',
  'end',
  'renewal_type',
  'renewal_notice_days',
  'termination_notice_days',
  'payment_days',
  'payment_end_of_month',
  'billing_cadence',
  'currency',
  'customer_share_cap',
  'expense_policy',
  'note',
]

/**
 * One dialog behind both «Nuovo contratto» and «Modifica». It owns its mutations, like
 * `CostForm`, so the caller's handler and the form's own `mutate` can never both fire a
 * POST. The state is not a field here: it moves through its own action on the page
 * (`ContractStateActions`), one named act and one row in the timeline.
 */
export function ContractForm({ open, onOpenChange, customerId, contract, onSaved }: Props) {
  const isCreate = contract === undefined
  // The baseline an edit is compared with is the contract as the dialog opened on it: a
  // background refresh that brings someone else's change must not make this save send
  // the old values of fields this person never touched.
  const [initial, setInitial] = useState<ContractFormValues | undefined>(
    contract === undefined ? undefined : contractToFormValues(contract),
  )
  const [values, setValues] = useState<ContractFormValues>(initial ?? defaultContractFormValues())
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const schema = useEntitySchema('contract')
  const customFields = schema.data?.custom_fields ?? []
  const create = useCreateContract()
  const update = useUpdateContract(contract?.id ?? '')

  // Same "reset synchronously when `open` toggles" pattern as the other dialogs: this
  // component stays mounted across opens, so a `useState` initialiser runs only once.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      const opened = contract === undefined ? undefined : contractToFormValues(contract)
      setInitial(opened)
      setValues(opened ?? defaultContractFormValues())
      setProblem(null)
    }
  }

  function set<K extends keyof ContractFormValues>(key: K, value: ContractFormValues[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  const isRenderedCustomKey = (key: string) => customFields.some((field) => field.key === key)

  function submit() {
    const body = toContractBody(values, initial, isRenderedCustomKey)
    setProblem(null)
    if (!isCreate && Object.keys(body).length === 0) {
      // Nothing moved: no request, and no «modificato» row in the contract's history.
      onOpenChange(false)
      return
    }
    if (isCreate) {
      if (customerId === undefined) return
      create.mutate(
        { ...(body as ContractCreateBody), customer_id: customerId },
        {
          onSuccess: (created) => {
            onOpenChange(false)
            toast.success('Contratto creato')
            onSaved?.(created)
          },
          onError: (error) => setProblem(toProblem(error)),
        },
      )
      return
    }
    update.mutate(body, {
      onSuccess: (saved) => {
        onOpenChange(false)
        toast.success('Contratto aggiornato')
        onSaved?.(saved)
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const busy = create.isPending || update.isPending
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  // A problem that names none of the controls drawn here (a 409, a 403) still has to be
  // read: shown once above the buttons rather than swallowed.
  const drawn = fieldError !== null && (DRAWN_FIELDS.includes(fieldError.field) || isRenderedCustomKey(fieldError.field))
  const unattributed =
    problem === null || drawn
      ? null
      : fieldError === null
        ? problem.detail
        : `${fieldError.field}: ${fieldError.message}`

  const cadences: string[] = CADENCES.includes(values.billing_cadence as (typeof CADENCES)[number])
    ? [...CADENCES]
    : [...CADENCES, values.billing_cadence]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isCreate ? 'Nuovo contratto' : 'Modifica contratto'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="contract-title">Titolo</Label>
          <Input
            id="contract-title"
            value={values.title}
            onChange={(event) => set('title', event.target.value)}
            aria-invalid={fieldError?.field === 'title'}
          />
          {errorOn('title')}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="contract-start">Inizio</Label>
            <Input
              id="contract-start"
              type="date"
              value={values.start}
              onChange={(event) => set('start', event.target.value)}
              aria-invalid={fieldError?.field === 'start'}
            />
            {errorOn('start')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="contract-fine">Fine</Label>
            <Input
              id="contract-fine"
              type="date"
              value={values.end}
              onChange={(event) => set('end', event.target.value)}
              aria-invalid={fieldError?.field === 'end'}
            />
            <p className="text-xs text-muted-foreground">Vuota se il contratto non ha scadenza.</p>
            {errorOn('end')}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="contract-renewal">Rinnovo</Label>
            <Select
              value={values.renewal_type}
              onValueChange={(value) => set('renewal_type', value as RenewalType)}
            >
              <SelectTrigger id="contract-renewal" className="w-full">
                <SelectValue placeholder={EMPTY_SELECT_HINT} />
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
              <Label htmlFor="contract-notice-renewal">Preavviso di rinnovo (giorni)</Label>
              <Input
                id="contract-notice-renewal"
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

        <div className="space-y-2">
          <Label htmlFor="contract-notice-termination">Preavviso di disdetta (giorni)</Label>
          <Input
            id="contract-notice-termination"
            type="number"
            min={0}
            value={values.termination_notice_days}
            onChange={(event) => set('termination_notice_days', event.target.value)}
            aria-invalid={fieldError?.field === 'termination_notice_days'}
          />
          {errorOn('termination_notice_days')}
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="contract-custom-payment"
              checked={values.payment_custom}
              onCheckedChange={(checked) => set('payment_custom', checked === true)}
            />
            <Label htmlFor="contract-custom-payment" className="font-normal">
              Termini di pagamento propri del contratto
            </Label>
          </div>
          {values.payment_custom ? (
            <div className="grid grid-cols-2 items-end gap-4">
              <div className="space-y-2">
                <Label htmlFor="contract-days-payment">Giorni di pagamento</Label>
                <Input
                  id="contract-days-payment"
                  type="number"
                  min={0}
                  value={values.payment_days}
                  onChange={(event) => set('payment_days', event.target.value)}
                  aria-invalid={fieldError?.field === 'payment_days'}
                />
                {errorOn('payment_days')}
              </div>
              <div className="flex items-center gap-2 pb-2">
                <Checkbox
                  id="contract-end-month"
                  checked={values.payment_end_of_month}
                  onCheckedChange={(checked) => set('payment_end_of_month', checked === true)}
                />
                <Label htmlFor="contract-end-month" className="font-normal">
                  Scadenza a fine mese
                </Label>
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Senza termini propri valgono quelli del cliente.
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="contract-cadenza">Cadenza di fatturazione</Label>
            <Select
              value={values.billing_cadence}
              onValueChange={(value) => set('billing_cadence', value)}
            >
              <SelectTrigger id="contract-cadenza" className="w-full">
                <SelectValue placeholder={EMPTY_SELECT_HINT} />
              </SelectTrigger>
              <SelectContent>
                {cadences.map((cadence) => (
                  <SelectItem key={cadence} value={cadence}>
                    {cadenceLabel(cadence)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('billing_cadence')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="contract-currency">Divisa</Label>
            <Input
              id="contract-currency"
              maxLength={3}
              value={values.currency}
              onChange={(event) => set('currency', event.target.value)}
              aria-invalid={fieldError?.field === 'currency'}
            />
            {errorOn('currency')}
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="contract-approval"
              checked={values.requires_prior_approval}
              onCheckedChange={(checked) => set('requires_prior_approval', checked === true)}
            />
            <Label htmlFor="contract-approval" className="font-normal">
              Richiede l’approvazione preventiva del cliente
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="contract-surcharge"
              checked={values.applies_social_charge}
              onCheckedChange={(checked) => set('applies_social_charge', checked === true)}
            />
            <Label htmlFor="contract-surcharge" className="font-normal">
              Applica la rivalsa INPS in fattura
            </Label>
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="contract-cap-share">Tetto sulla quota del cliente (%)</Label>
          <Input
            id="contract-cap-share"
            inputMode="decimal"
            placeholder="50"
            value={values.customer_share_cap}
            onChange={(event) => set('customer_share_cap', event.target.value)}
            aria-invalid={fieldError?.field === 'customer_share_cap'}
            aria-describedby="contract-cap-share-hint"
          />
          <p id="contract-cap-share-hint" className="text-xs text-muted-foreground">
            La quota del cliente sui ricavi fatturati dell’azienda che non deve raggiungere, sull’anno di
            anniversario del contratto. Vuoto se non c’è un tetto.
          </p>
          {errorOn('customer_share_cap')}
        </div>

        <div className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="contract-expenses">Politica sulle spese</Label>
            <Select
              value={values.expenses_kind}
              onValueChange={(value) => set('expenses_kind', value as ExpensePolicyKind)}
            >
              <SelectTrigger id="contract-expenses" className="w-full">
                <SelectValue placeholder={EMPTY_SELECT_HINT} />
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
              <Label htmlFor="contract-cap">Tetto di rimborso</Label>
              <Input
                id="contract-cap"
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
                id="contract-preauthorization"
                checked={values.expenses_preauthorization}
                onCheckedChange={(checked) => set('expenses_preauthorization', checked === true)}
              />
              <Label htmlFor="contract-preauthorization" className="font-normal">
                Ogni spesa va autorizzata prima
              </Label>
            </div>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="contract-note">Note</Label>
          <Textarea
            id="contract-note"
            value={values.note}
            onChange={(event) => set('note', event.target.value)}
          />
          {errorOn('note')}
        </div>

        {customFields.length > 0 && (
          <DynamicForm
            fields={customFields}
            values={values.custom}
            onChange={(key, value) => set('custom', { ...values.custom, [key]: value })}
            problem={fieldError !== null && isRenderedCustomKey(fieldError.field) ? problem : null}
            mode={isCreate ? 'create' : 'edit'}
          />
        )}

        {unattributed !== null && <p className="text-sm text-destructive">{unattributed}</p>}

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
