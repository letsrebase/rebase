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
  'titolo',
  'inizio',
  'fine',
  'tipo_rinnovo',
  'preavviso_rinnovo_giorni',
  'preavviso_disdetta_giorni',
  'giorni_pagamento',
  'pagamento_fine_mese',
  'cadenza_fatturazione',
  'divisa',
  'politica_spese',
  'note',
]

/**
 * One dialog behind both «Nuovo contratto» and «Modifica». It owns its mutations, like
 * `CostForm`, so the caller's handler and the form's own `mutate` can never both fire a
 * POST. The state is not a field here: it moves through its own action on the page
 * (`ContractStateActions`), one named act and one row in the timeline.
 */
export function ContractForm({ open, onOpenChange, customerId, contract, onSaved }: Props) {
  const initial = contract === undefined ? undefined : contractToFormValues(contract)
  const isCreate = contract === undefined
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
      setValues(initial ?? defaultContractFormValues())
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

  const cadences: string[] = CADENCES.includes(values.cadenza_fatturazione as (typeof CADENCES)[number])
    ? [...CADENCES]
    : [...CADENCES, values.cadenza_fatturazione]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isCreate ? 'Nuovo contratto' : 'Modifica contratto'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="contract-titolo">Titolo</Label>
          <Input
            id="contract-titolo"
            value={values.titolo}
            onChange={(event) => set('titolo', event.target.value)}
            aria-invalid={fieldError?.field === 'titolo'}
          />
          {errorOn('titolo')}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="contract-inizio">Inizio</Label>
            <Input
              id="contract-inizio"
              type="date"
              value={values.inizio}
              onChange={(event) => set('inizio', event.target.value)}
              aria-invalid={fieldError?.field === 'inizio'}
            />
            {errorOn('inizio')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="contract-fine">Fine</Label>
            <Input
              id="contract-fine"
              type="date"
              value={values.fine}
              onChange={(event) => set('fine', event.target.value)}
              aria-invalid={fieldError?.field === 'fine'}
            />
            <p className="text-xs text-muted-foreground">Vuota se il contratto non ha scadenza.</p>
            {errorOn('fine')}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="contract-rinnovo">Rinnovo</Label>
            <Select
              value={values.tipo_rinnovo}
              onValueChange={(value) => set('tipo_rinnovo', value as RenewalType)}
            >
              <SelectTrigger id="contract-rinnovo" className="w-full">
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
            {errorOn('tipo_rinnovo')}
          </div>
          {values.tipo_rinnovo !== 'nessuno' && (
            <div className="space-y-2">
              <Label htmlFor="contract-preavviso-rinnovo">Preavviso di rinnovo (giorni)</Label>
              <Input
                id="contract-preavviso-rinnovo"
                type="number"
                min={0}
                value={values.preavviso_rinnovo_giorni}
                onChange={(event) => set('preavviso_rinnovo_giorni', event.target.value)}
                aria-invalid={fieldError?.field === 'preavviso_rinnovo_giorni'}
              />
              {errorOn('preavviso_rinnovo_giorni')}
            </div>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="contract-preavviso-disdetta">Preavviso di disdetta (giorni)</Label>
          <Input
            id="contract-preavviso-disdetta"
            type="number"
            min={0}
            value={values.preavviso_disdetta_giorni}
            onChange={(event) => set('preavviso_disdetta_giorni', event.target.value)}
            aria-invalid={fieldError?.field === 'preavviso_disdetta_giorni'}
          />
          {errorOn('preavviso_disdetta_giorni')}
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="contract-pagamento-proprio"
              checked={values.pagamento_proprio}
              onCheckedChange={(checked) => set('pagamento_proprio', checked === true)}
            />
            <Label htmlFor="contract-pagamento-proprio" className="font-normal">
              Termini di pagamento propri del contratto
            </Label>
          </div>
          {values.pagamento_proprio ? (
            <div className="grid grid-cols-2 items-end gap-4">
              <div className="space-y-2">
                <Label htmlFor="contract-giorni-pagamento">Giorni di pagamento</Label>
                <Input
                  id="contract-giorni-pagamento"
                  type="number"
                  min={0}
                  value={values.giorni_pagamento}
                  onChange={(event) => set('giorni_pagamento', event.target.value)}
                  aria-invalid={fieldError?.field === 'giorni_pagamento'}
                />
                {errorOn('giorni_pagamento')}
              </div>
              <div className="flex items-center gap-2 pb-2">
                <Checkbox
                  id="contract-fine-mese"
                  checked={values.pagamento_fine_mese}
                  onCheckedChange={(checked) => set('pagamento_fine_mese', checked === true)}
                />
                <Label htmlFor="contract-fine-mese" className="font-normal">
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
              value={values.cadenza_fatturazione}
              onValueChange={(value) => set('cadenza_fatturazione', value)}
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
            {errorOn('cadenza_fatturazione')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="contract-divisa">Divisa</Label>
            <Input
              id="contract-divisa"
              maxLength={3}
              value={values.divisa}
              onChange={(event) => set('divisa', event.target.value)}
              aria-invalid={fieldError?.field === 'divisa'}
            />
            {errorOn('divisa')}
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="contract-approvazione"
              checked={values.requires_prior_approval}
              onCheckedChange={(checked) => set('requires_prior_approval', checked === true)}
            />
            <Label htmlFor="contract-approvazione" className="font-normal">
              Richiede l’approvazione preventiva del cliente
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="contract-rivalsa"
              checked={values.applies_social_charge}
              onCheckedChange={(checked) => set('applies_social_charge', checked === true)}
            />
            <Label htmlFor="contract-rivalsa" className="font-normal">
              Applica la rivalsa INPS in fattura
            </Label>
          </div>
        </div>

        <div className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="contract-spese">Politica sulle spese</Label>
            <Select
              value={values.spese_kind}
              onValueChange={(value) => set('spese_kind', value as ExpensePolicyKind)}
            >
              <SelectTrigger id="contract-spese" className="w-full">
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
            {errorOn('politica_spese')}
          </div>
          {values.spese_kind === 'rimborsabile_con_tetto' && (
            <div className="space-y-2">
              <Label htmlFor="contract-tetto">Tetto di rimborso</Label>
              <Input
                id="contract-tetto"
                inputMode="decimal"
                placeholder="500.00"
                value={values.spese_tetto}
                onChange={(event) => set('spese_tetto', event.target.value)}
              />
            </div>
          )}
          {values.spese_kind !== 'non_rimborsabile' && (
            <div className="flex items-center gap-2">
              <Checkbox
                id="contract-preautorizzazione"
                checked={values.spese_preautorizzazione}
                onCheckedChange={(checked) => set('spese_preautorizzazione', checked === true)}
              />
              <Label htmlFor="contract-preautorizzazione" className="font-normal">
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
