import { useState } from 'react'
import { Button } from '@rebase/ui/button'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { decimalString } from '@/features/cash/account/labels'
import { TAX_LABELS, type Tax } from './labels'
import {
  failureText,
  useCreateNoticePlan,
  useUpdateNoticePlan,
  type NoticePlanSet,
  type TaxNoticePlan,
} from './queries'

interface Row {
  due_date: string
  interest: string
}

const NO_INTEREST = ''

/** The amount as it is typed again: «12400.00» is shown «12400.00», the person may write
 *  «12.400,00»; `decimalString` reads both and refuses the rest. */
function fromPlan(plan: TaxNoticePlan): { label: string; tax: Tax; total: string; rows: Row[] } {
  return {
    label: plan.label,
    tax: plan.tax as Tax,
    total: plan.total,
    rows: plan.instalments.map((item) => ({ due_date: item.due_date, interest: item.interest })),
  }
}

const TAXES = Object.keys(TAX_LABELS) as Tax[]

/**
 * A payment plan from a notice of the tax office («avviso bonario», «cartella»), typed by hand:
 * what it is, the tax, the total it asks before interest, and each instalment's day and
 * interest. The principal is split equally by the server, which also says why it refuses a
 * plan (dates out of order, a payment on an instalment). A dense two-column form, the action bar
 * at the bottom.
 */
export function NoticePlanDialog({
  legalEntityId,
  plan,
  onClose,
}: {
  legalEntityId: string
  /** The plan being changed; absent, a new one. */
  plan?: TaxNoticePlan
  onClose: () => void
}) {
  const start = plan
    ? fromPlan(plan)
    : { label: '', tax: 'irpef' as Tax, total: '', rows: [{ due_date: '', interest: NO_INTEREST }] }
  const [label, setLabel] = useState(start.label)
  const [tax, setTax] = useState<Tax>(start.tax)
  const [total, setTotal] = useState(start.total)
  const [rows, setRows] = useState<Row[]>(start.rows)
  const [problem, setProblem] = useState<string | null>(null)
  const create = useCreateNoticePlan(legalEntityId)
  const update = useUpdateNoticePlan(plan?.id ?? '')
  const busy = create.isPending || update.isPending

  function setRow(index: number, patch: Partial<Row>) {
    setRows((previous) => previous.map((row, at) => (at === index ? { ...row, ...patch } : row)))
  }

  function submit() {
    const sum = decimalString(total)
    if (label.trim() === '') return setProblem('Indica di che avviso si tratta.')
    if (sum === null || sum === '') return setProblem('L’importo dell’avviso non è chiaro: scrivilo come 12.400,00.')
    const instalments: NoticePlanSet['instalments'] = []
    for (const row of rows) {
      const interest = decimalString(row.interest)
      if (row.due_date === '') return setProblem('Ogni rata ha una data.')
      if (interest === null) return setProblem('Gli interessi di una rata non sono chiari: scrivili come 12,50.')
      instalments.push({ due_date: row.due_date, interest: interest === '' ? '0.00' : interest })
    }
    setProblem(null)
    const body = { label: label.trim(), tax, total: sum, instalments }
    const options = { onSuccess: onClose, onError: (error: unknown) => setProblem(failureText(error)) }
    if (plan) update.mutate(body, options)
    else create.mutate(body, options)
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{plan ? 'Modifica il piano di rate' : 'Nuovo piano di rate'}</DialogTitle>
          <DialogDescription>
            Il piano di un avviso dell’Agenzia delle Entrate. L’importo si divide in parti uguali, ogni
            rata aggiunge i suoi interessi.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="notice-label">Avviso</Label>
            <Input
              id="notice-label"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="Avviso bonario 2023"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="notice-tax">Imposta</Label>
            <Select value={tax} onValueChange={(next) => setTax(next as Tax)}>
              <SelectTrigger id="notice-tax" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TAXES.map((name) => (
                  <SelectItem key={name} value={name}>
                    {TAX_LABELS[name]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="notice-total">Importo dell’avviso, senza interessi</Label>
            <Input
              id="notice-total"
              value={total}
              onChange={(event) => setTotal(event.target.value)}
              inputMode="decimal"
              className="tabular-nums"
              placeholder="12.400,00"
            />
          </div>
        </div>
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">Rate</legend>
          {rows.map((row, index) => (
            <div key={index} className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
              <div className="space-y-2">
                <Label htmlFor={`notice-date-${index}`}>Scadenza della rata {index + 1}</Label>
                <Input
                  id={`notice-date-${index}`}
                  type="date"
                  value={row.due_date}
                  onChange={(event) => setRow(index, { due_date: event.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`notice-interest-${index}`}>Interessi della rata {index + 1}</Label>
                <Input
                  id={`notice-interest-${index}`}
                  value={row.interest}
                  onChange={(event) => setRow(index, { interest: event.target.value })}
                  inputMode="decimal"
                  className="tabular-nums"
                  placeholder="0,00"
                />
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={rows.length === 1}
                onClick={() => setRows((previous) => previous.filter((_, at) => at !== index))}
                aria-label={`Togli la rata ${index + 1}`}
              >
                Togli
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setRows((previous) => [...previous, { due_date: '', interest: NO_INTEREST }])}
          >
            Aggiungi una rata
          </Button>
        </fieldset>
        {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={busy}>
            {plan ? 'Salva le modifiche' : 'Registra il piano'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
