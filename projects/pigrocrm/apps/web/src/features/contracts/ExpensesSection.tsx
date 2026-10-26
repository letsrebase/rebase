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
import { useCostCategories } from '@/features/costs/queries'
import { formatDate, formatMoney } from '@/features/invoices/format'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { expenseStanding } from './labels'
import {
  useContractExpenses,
  useCreateContractExpense,
  useUpdateContractExpense,
  type ContractExpense,
} from './queries'

interface ExpenseFormValues {
  category_id: string
  data: string
  importo: string
  descrizione: string
  pre_autorizzata: boolean
  riferimento_autorizzazione: string
}

function emptyForm(): ExpenseFormValues {
  return {
    category_id: '',
    data: toIsoDate(new Date()),
    importo: '',
    descrizione: '',
    pre_autorizzata: false,
    riferimento_autorizzazione: '',
  }
}

function toFormValues(expense: ContractExpense): ExpenseFormValues {
  return {
    category_id: expense.category_id,
    data: expense.data,
    importo: String(expense.importo),
    descrizione: expense.descrizione,
    pre_autorizzata: expense.pre_autorizzata,
    riferimento_autorizzazione: expense.riferimento_autorizzazione ?? '',
  }
}

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contractId: string
  expense?: ContractExpense
}

function ExpenseDialog({ open, onOpenChange, contractId, expense }: DialogProps) {
  const isCreate = expense === undefined
  const initial = expense === undefined ? emptyForm() : toFormValues(expense)
  const [values, setValues] = useState<ExpenseFormValues>(initial)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const categories = useCostCategories()
  const create = useCreateContractExpense(contractId)
  const update = useUpdateContractExpense(contractId, expense?.id ?? '')

  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(initial)
      setProblem(null)
    }
  }

  function set<K extends keyof ExpenseFormValues>(key: K, value: ExpenseFormValues[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  function submit() {
    const body = {
      category_id: values.category_id,
      data: values.data,
      importo: values.importo.trim().replace(',', '.'),
      descrizione: values.descrizione.trim(),
      pre_autorizzata: values.pre_autorizzata,
      riferimento_autorizzazione: values.pre_autorizzata
        ? values.riferimento_autorizzazione.trim()
        : null,
    }
    setProblem(null)
    const callbacks = {
      onSuccess: () => {
        onOpenChange(false)
        toast.success(isCreate ? 'Spesa registrata' : 'Spesa aggiornata')
      },
      onError: (error: unknown) => setProblem(toProblem(error)),
    }
    if (isCreate) create.mutate(body, callbacks)
    else update.mutate(body, callbacks)
  }

  const busy = create.isPending || update.isPending
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  const elsewhere = problem !== null && fieldError === null ? problem.detail : null
  // Archived categories stay on the rows that already carry them but take nothing new.
  const choices = (categories.data ?? []).filter(
    (category) => !category.archiviata || category.id === values.category_id,
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isCreate ? 'Nuova spesa' : 'Modifica spesa'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="expense-category">Categoria</Label>
          <Select value={values.category_id} onValueChange={(value) => set('category_id', value)}>
            <SelectTrigger id="expense-category" className="w-full">
              <SelectValue placeholder="Seleziona…" />
            </SelectTrigger>
            <SelectContent>
              {choices.map((category) => (
                <SelectItem key={category.id} value={category.id}>
                  {category.nome}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errorOn('category_id')}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="expense-data">Data</Label>
            <Input
              id="expense-data"
              type="date"
              value={values.data}
              onChange={(event) => set('data', event.target.value)}
              aria-invalid={fieldError?.field === 'data'}
            />
            {errorOn('data')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="expense-importo">Importo (€)</Label>
            <Input
              id="expense-importo"
              inputMode="decimal"
              placeholder="42.00"
              value={values.importo}
              onChange={(event) => set('importo', event.target.value)}
              aria-invalid={fieldError?.field === 'importo'}
            />
            {errorOn('importo')}
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="expense-descrizione">Descrizione</Label>
          <Input
            id="expense-descrizione"
            value={values.descrizione}
            onChange={(event) => set('descrizione', event.target.value)}
            aria-invalid={fieldError?.field === 'descrizione'}
          />
          {errorOn('descrizione')}
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="expense-preautorizzata"
              checked={values.pre_autorizzata}
              onCheckedChange={(checked) => set('pre_autorizzata', checked === true)}
            />
            <Label htmlFor="expense-preautorizzata" className="font-normal">
              Autorizzata prima dal cliente
            </Label>
          </div>
          {values.pre_autorizzata && (
            <div className="space-y-2">
              <Label htmlFor="expense-riferimento">Riferimento dell’autorizzazione</Label>
              <Input
                id="expense-riferimento"
                placeholder="email del 2 marzo"
                value={values.riferimento_autorizzazione}
                onChange={(event) => set('riferimento_autorizzazione', event.target.value)}
                aria-invalid={fieldError?.field === 'riferimento_autorizzazione'}
              />
              {errorOn('riferimento_autorizzazione')}
            </div>
          )}
        </div>

        <p className="text-xs text-muted-foreground">
          Se la spesa si può rifatturare lo decide il contratto: la politica sulle spese e
          l’autorizzazione qui sopra.
        </p>

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

/**
 * The «Spese» section of a contract's page (REB-743): every expense recorded on the
 * contract, oldest first, each saying whether it can be rebilled and whether it already
 * went onto an invoice. One that is rebillable and not yet billed reads «Da fatturare»,
 * which is what the invoice assembly picks up. An expense already on an invoice has no
 * edit on this page: its amount is on a document by now, and changing it from here
 * would leave the two disagreeing. That is this page's restraint and not a rule of the
 * API, which still accepts the PATCH.
 */
export function ExpensesSection({ contractId }: { contractId: string }) {
  const canWrite = useCan('create_contract_expense')
  const expenses = useContractExpenses(contractId)
  const categories = useCostCategories(true)
  const [dialog, setDialog] = useState<{ open: boolean; expense?: ContractExpense }>({
    open: false,
  })
  const categoryName = new Map((categories.data ?? []).map((category) => [category.id, category.nome]))

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">Spese</h2>
          {canWrite && (
            <Button size="sm" variant="outline" onClick={() => setDialog({ open: true })}>
              <Plus className="mr-2 size-4" />
              Nuova spesa
            </Button>
          )}
        </div>

        {expenses.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : expenses.isError ? (
          <QueryErrorBanner error={expenses.error} />
        ) : (expenses.data ?? []).length === 0 ? (
          <p className="text-muted-foreground">Nessuna spesa registrata su questo contratto.</p>
        ) : (
          <ul className="divide-y">
            {(expenses.data ?? []).map((expense) => {
              const standing = expenseStanding(expense)
              return (
                <li key={expense.id} className="flex items-center gap-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium">{expense.descrizione}</span>
                      <StatusPill tone={standing.tone}>{standing.label}</StatusPill>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {formatDate(expense.data)} · {categoryName.get(expense.category_id) ?? '—'}
                      {expense.pre_autorizzata
                        ? ` · autorizzata: ${expense.riferimento_autorizzazione ?? ''}`
                        : ''}
                    </p>
                  </div>
                  <span className="shrink-0 tabular-nums">{formatMoney(String(expense.importo))}</span>
                  {canWrite && expense.invoice_line_id === null && (
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`Modifica la spesa ${expense.descrizione}`}
                      onClick={() => setDialog({ open: true, expense })}
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

      <ExpenseDialog
        open={dialog.open}
        onOpenChange={(open) => setDialog((previous) => ({ ...previous, open }))}
        contractId={contractId}
        expense={dialog.expense}
      />
    </Card>
  )
}
