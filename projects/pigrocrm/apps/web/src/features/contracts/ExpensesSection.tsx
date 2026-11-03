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
import { formatDate } from '@/features/invoices/format'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { expenseStanding, currencySign, formatAmount } from './labels'
import {
  useContractExpenses,
  useCreateContractExpense,
  useUpdateContractExpense,
  type ContractExpense,
  type ContractExpenseCreateBody,
} from './queries'

interface ExpenseFormValues {
  category_id: string
  date: string
  amount: string
  description: string
  pre_authorized: boolean
  authorization_reference: string
}

function emptyForm(): ExpenseFormValues {
  return {
    category_id: '',
    date: toIsoDate(new Date()),
    amount: '',
    description: '',
    pre_authorized: false,
    authorization_reference: '',
  }
}

function toFormValues(expense: ContractExpense): ExpenseFormValues {
  return {
    category_id: expense.category_id,
    date: expense.date,
    amount: String(expense.amount),
    description: expense.description,
    pre_authorized: expense.pre_authorized,
    authorization_reference: expense.authorization_reference ?? '',
  }
}

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contractId: string
  currency: string
  expense?: ContractExpense
}

function ExpenseDialog({ open, onOpenChange, contractId, currency, expense }: DialogProps) {
  const isCreate = expense === undefined
  const initial = expense === undefined ? emptyForm() : toFormValues(expense)
  const [values, setValues] = useState<ExpenseFormValues>(initial)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  // Archived ones too: the expense being edited may sit under one, and the dialog has to
  // show it. `choices` below keeps them out of a new expense's list.
  const categories = useCostCategories(true)
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
    // The category is sent only when it changed: an archived one is refused by the API
    // for a new assignment, and an edit of the amount must not trip on it.
    const body = {
      ...(isCreate || values.category_id !== expense.category_id
        ? { category_id: values.category_id }
        : {}),
      date: values.date,
      amount: values.amount.trim().replace(',', '.'),
      description: values.description.trim(),
      pre_authorized: values.pre_authorized,
      authorization_reference: values.pre_authorized
        ? values.authorization_reference.trim()
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
    if (isCreate) create.mutate(body as ContractExpenseCreateBody, callbacks)
    else update.mutate(body, callbacks)
  }

  const busy = create.isPending || update.isPending
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  // Whatever no control answers for (the model-level check that a pre-authorised expense
  // names its reference reaches us as field `body`) is shown here, or saving would seem
  // to do nothing.
  const drawn =
    fieldError !== null &&
    [
      'category_id',
      'date',
      'amount',
      'description',
      'authorization_reference',
    ].includes(fieldError.field)
  const elsewhere =
    problem !== null && !drawn ? (fieldError === null ? problem.detail : fieldError.message) : null
  // Archived categories stay on the rows that already carry them but take nothing new.
  const choices = (categories.data ?? []).filter(
    (category) => !category.archived || category.id === values.category_id,
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
                  {category.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errorOn('category_id')}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="expense-date">Data</Label>
            <Input
              id="expense-date"
              type="date"
              value={values.date}
              onChange={(event) => set('date', event.target.value)}
              aria-invalid={fieldError?.field === 'date'}
            />
            {errorOn('date')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="expense-amount">Importo ({currencySign(currency)})</Label>
            <Input
              id="expense-amount"
              inputMode="decimal"
              placeholder="42.00"
              value={values.amount}
              onChange={(event) => set('amount', event.target.value)}
              aria-invalid={fieldError?.field === 'amount'}
            />
            {errorOn('amount')}
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="expense-description">Descrizione</Label>
          <Input
            id="expense-description"
            value={values.description}
            onChange={(event) => set('description', event.target.value)}
            aria-invalid={fieldError?.field === 'description'}
          />
          {errorOn('description')}
        </div>

        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="expense-preautorizzata"
              checked={values.pre_authorized}
              onCheckedChange={(checked) => set('pre_authorized', checked === true)}
            />
            <Label htmlFor="expense-preautorizzata" className="font-normal">
              Autorizzata prima dal cliente
            </Label>
          </div>
          {values.pre_authorized && (
            <div className="space-y-2">
              <Label htmlFor="expense-reference">Riferimento dell’autorizzazione</Label>
              <Input
                id="expense-reference"
                placeholder="email del 2 marzo"
                value={values.authorization_reference}
                onChange={(event) => set('authorization_reference', event.target.value)}
                aria-invalid={fieldError?.field === 'authorization_reference'}
              />
              {errorOn('authorization_reference')}
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
export function ExpensesSection({ contractId, currency }: { contractId: string; currency: string }) {
  const canWrite = useCan('create_contract_expense')
  const expenses = useContractExpenses(contractId)
  const categories = useCostCategories(true)
  const [dialog, setDialog] = useState<{ open: boolean; expense?: ContractExpense }>({
    open: false,
  })
  const categoryName = new Map((categories.data ?? []).map((category) => [category.id, category.name]))

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
                <li key={expense.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                  {/* A text column no narrower than 11rem: below it the amount and the pencil
                      drop to a line of their own instead of squeezing the words over each other. */}
                  <div className="min-w-44 flex-1">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium">{expense.description}</span>
                      <StatusPill tone={standing.tone}>{standing.label}</StatusPill>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {formatDate(expense.date)} · {categoryName.get(expense.category_id) ?? '—'}
                      {expense.pre_authorized
                        ? ` · autorizzata: ${expense.authorization_reference ?? ''}`
                        : ''}
                    </p>
                  </div>
                  <span className="shrink-0 tabular-nums">{formatAmount(String(expense.amount), currency)}</span>
                  {canWrite && expense.invoice_line_id === null && (
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`Modifica la spesa ${expense.description}`}
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
        currency={currency}
        expense={dialog.expense}
      />
    </Card>
  )
}
