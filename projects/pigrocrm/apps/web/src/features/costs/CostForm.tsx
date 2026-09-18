import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DynamicForm } from '@/components/DynamicForm'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import type { FieldDefinition } from '@/lib/schema'
import {
  NATIVE_FIELDS,
  defaultCostFormValues,
  toCostRequestBody,
  type CostFormValues,
} from './formValues'
import { useCreateCost, useDeleteCost, useUpdateCost, type CostCategory } from './queries'

export interface CostFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Absent means a general expense (§7.4): a cost that belongs to the business and to
   *  no deal. Never defaulted to some deal to keep the body simple -- that is exactly
   *  how a general expense would silently become one client's cost. */
  dealId?: string
  categories: CostCategory[]
  customFields: FieldDefinition[]
  /** Absent means "compose a new cost". Derived rather than a separate boolean prop, as
   *  `DealForm` and `TimeEntryForm` both do it, so "which mode" and "what is seeded"
   *  can never disagree. */
  initial?: CostFormValues
  costId?: string
  title: string
  onSaved?: () => void
}

/**
 * The one dialog behind both «Registra costo» and «Modifica costo».
 *
 * It owns its mutations rather than taking an `onSubmit`, for the same reason
 * `TimeEntryForm` does: with both, the caller's handler and this component's own
 * `mutate` each fire a POST and the same expense is recorded twice.
 */
export function CostForm({
  open,
  onOpenChange,
  dealId,
  categories,
  customFields,
  initial,
  costId,
  title,
  onSaved,
}: CostFormProps) {
  const [values, setValues] = useState<CostFormValues>(initial ?? defaultCostFormValues())
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const isCreate = initial === undefined
  const create = useCreateCost()
  const update = useUpdateCost(costId ?? '')
  const remove = useDeleteCost()

  // Same "reset synchronously when `open` toggles" pattern as CustomerForm/DealForm/
  // TimeEntryForm: this component stays mounted across dialog opens, so a plain
  // useState initializer would only ever run once.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(initial ?? defaultCostFormValues())
      setProblem(null)
    }
  }

  const isRenderedCustomKey = (key: string) => customFields.some((field) => field.key === key)

  function change(key: string, value: unknown) {
    setValues((previous) =>
      isRenderedCustomKey(key) || key in previous.custom
        ? { ...previous, custom: { ...previous.custom, [key]: value } }
        : { ...previous, native: { ...previous.native, [key]: value } },
    )
  }

  function submit() {
    const body = toCostRequestBody(values, { initial, isRenderedCustomKey })
    setProblem(null)
    if (isCreate) {
      // `deal_id` comes from the route, never from a control. `?? null` rather than an
      // omitted key: `null` is the API's own spelling of "spesa generale", and leaving
      // it out would be indistinguishable from forgetting it.
      create.mutate(
        { ...body, deal_id: dealId ?? null },
        {
          onSuccess: () => {
            onOpenChange(false)
            onSaved?.()
            toast.success('Costo registrato')
          },
          onError: (error) => setProblem(toProblem(error)),
        },
      )
      return
    }
    update.mutate(body, {
      onSuccess: () => {
        onSaved?.()
        toast.success('Costo aggiornato')
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function archive() {
    // The same cheap misclick guard the other forms use, and the wording says what
    // actually happens: `soft_delete` sets `deleted_at`, so nothing is lost.
    const confirmed = window.confirm(
      'Eliminare questo costo? Sparisce dagli elenchi e dai conti del deal, ma il dato resta e l’operazione è reversibile.',
    )
    if (!confirmed || costId === undefined) return
    remove.mutate(costId, {
      onSuccess: () => {
        onSaved?.()
        toast.success('Costo eliminato')
      },
      // A closed period refuses the delete with its own sentence: shown here rather
      // than swallowed, for the same reason a failed save is.
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const busy = create.isPending || update.isPending || remove.isPending
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const categoryId = (values.native.category_id as string | undefined) ?? ''

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="cost-category">Categoria</Label>
          {/* Its own control rather than a `select` field handed to `DynamicForm`: that
              renderer submits the option string itself, and this value has to be the
              category's UUID so a later rename does not orphan the cost. Only the
              active categories are offered -- an archived one is still shown on rows
              that already reference it, but nothing new should be filed under it. */}
          <Select value={categoryId} onValueChange={(value) => change('category_id', value)}>
            <SelectTrigger id="cost-category" className="w-full">
              <SelectValue placeholder="Seleziona…" />
            </SelectTrigger>
            <SelectContent>
              {categories.map((category) => (
                <SelectItem key={category.id} value={category.id}>
                  {category.nome}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {fieldError?.field === 'category_id' && (
            <p className="text-sm text-destructive">{fieldError.message}</p>
          )}
        </div>

        <DynamicForm
          fields={[...NATIVE_FIELDS, ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can draw.
          // The state behind it stays split, so `toCostRequestBody` still knows which
          // namespace each value came from.
          values={{ ...values.native, ...values.custom }}
          onChange={change}
          // Withheld when the server blamed `category_id`: that key has no control
          // inside `DynamicForm`, so it would render the message a second time as a
          // raw `category_id: ...` banner next to the line already shown above.
          problem={fieldError?.field === 'category_id' ? null : problem}
          mode={isCreate ? 'create' : 'edit'}
        />

        <DialogFooter>
          {!isCreate && (
            <Button variant="destructive" className="mr-auto" onClick={archive} disabled={busy}>
              Elimina
            </Button>
          )}
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
