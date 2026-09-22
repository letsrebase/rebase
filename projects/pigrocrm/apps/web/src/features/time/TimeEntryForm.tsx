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
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { FieldDefinition } from '@/lib/schema'
import { formatRateValue } from './columns'
import {
  LOCKED_KEYS,
  NATIVE_FIELDS,
  defaultFormValues,
  toRequestBody,
  type TimeEntryFormValues,
} from './formValues'
import { useDealRates, useDeleteTimeEntry, useLogTime, useUpdateTimeEntry } from './queries'

export interface TimeEntryFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  dealId: string
  customFields: FieldDefinition[]
  /** Absent means "compose a new entry". Derived rather than a separate boolean prop,
   *  exactly as `DealForm` does it, so "which mode" and "what is seeded" can never
   *  disagree. */
  initial?: TimeEntryFormValues
  entryId?: string
  locked?: boolean
  title: string
  /** Called after the server accepted the write, never before -- the caller closes an
   *  edit dialog on this and nothing else. */
  onSaved?: () => void
}

/**
 * The one dialog behind both "Registra ore" and "Modifica voce".
 *
 * It owns its mutations rather than taking an `onSubmit` from the caller. That is not
 * cosmetic: with both, the caller's handler and this component's own `mutate` each fire
 * a POST, and the user silently logs the same hours twice. Owning the write also puts
 * the resulting problem document next to the controls that caused it, which is the only
 * place the server's own sentence is any use.
 */
export function TimeEntryForm({
  open,
  onOpenChange,
  dealId,
  customFields,
  initial,
  entryId,
  locked = false,
  title,
  onSaved,
}: TimeEntryFormProps) {
  const { user } = useAuth()
  const [values, setValues] = useState<TimeEntryFormValues>(initial ?? defaultFormValues())
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const isCreate = initial === undefined
  const rates = useDealRates(dealId, user?.id)
  const log = useLogTime()
  const update = useUpdateTimeEntry(entryId ?? '')
  const remove = useDeleteTimeEntry()

  // Same "reset synchronously when `open` toggles" pattern as CustomerForm/DealForm:
  // this component stays mounted across dialog opens, so a plain useState initializer
  // would only ever run once.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(initial ?? defaultFormValues())
      setProblem(null)
    }
  }

  /** True for a key the active schema currently renders as a custom-field control --
   *  only ever asked what the user can see, never what a value *is*. */
  const isRenderedCustomKey = (key: string) => customFields.some((field) => field.key === key)

  function change(key: string, value: unknown) {
    setValues((previous) =>
      isRenderedCustomKey(key) || key in previous.custom
        ? { ...previous, custom: { ...previous.custom, [key]: value } }
        : { ...previous, native: { ...previous.native, [key]: value } },
    )
  }

  function submit() {
    const body = toRequestBody(values, { initial, locked, isRenderedCustomKey })
    if (isCreate) {
      // `deal_id` and `user_id` come from the route and the session, never from a
      // control: an agent resolving the wrong deal attributes billable hours to the
      // wrong client, and a human picker here would be the same hazard with a mouse.
      log.mutate(
        { ...body, deal_id: dealId, user_id: user?.id },
        {
          onSuccess: () => {
            onOpenChange(false)
            onSaved?.()
            toast.success('Ore registrate')
          },
          onError: (error) => setProblem(toProblem(error)),
        },
      )
      return
    }
    update.mutate(body, {
      onSuccess: () => {
        onSaved?.()
        toast.success('Voce aggiornata')
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function archive() {
    // A plain confirm, the same cheap guard against a misclick `DealDetail.archive`
    // uses -- not a client-side re-implementation of any server rule. The deletion is
    // reversible (`soft_delete` sets `deleted_at`), which is exactly what the wording
    // says, so the user is not warned about a loss that does not happen.
    const confirmed = window.confirm(
      'Eliminare questa voce di ore? Sparisce dagli elenchi e dai rapporti, ma il dato resta e l’operazione è reversibile.',
    )
    if (!confirmed || entryId === undefined) return
    remove.mutate(
      { entryId, dealId },
      {
        onSuccess: () => {
          onSaved?.()
          toast.success('Voce eliminata')
        },
        // A closed period and a billed entry both refuse a delete, each with its own
        // sentence: shown here rather than swallowed, for the same reason a failed save
        // is.
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  const fields = [
    ...NATIVE_FIELDS.filter((field) => !(locked && LOCKED_KEYS.has(field.key))),
    ...customFields,
  ]
  const busy = log.isPending || update.isPending || remove.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        {locked && (
          <p className="border border-muted bg-muted/40 px-3 py-2 text-sm">
            La voce è su una fattura emessa: ore, data, tariffa e descrizione sono
            congelate. Restano modificabili le note interne e i campi personalizzati.
          </p>
        )}

        {isCreate && rates.data && (
          <p className="text-sm text-muted-foreground">
            Tariffa che verrà congelata su questa voce:{' '}
            <strong>{formatRateValue(rates.data.tariffa)}</strong>{' '}
            {rates.data.tariffa === null
              ? '— la voce sarà registrata senza tariffa'
              : `(${rates.data.tariffa_origine})`}
          </p>
        )}

        <DynamicForm
          fields={fields}
          // Flattened for rendering only -- one control per key is all a form can draw.
          // The state behind it stays split, so `toRequestBody` still knows which
          // namespace each value came from.
          values={{ ...values.native, ...values.custom }}
          onChange={change}
          problem={problem}
          mode={isCreate ? 'create' : 'edit'}
        />

        <DialogFooter>
          {/* Only on an entry that exists and that no invoice has claimed: the backend
              refuses to delete a billed row outright, so offering the button would only
              produce a conflict the user cannot act on. */}
          {!isCreate && !locked && (
            <Button
              variant="destructive"
              className="mr-auto"
              onClick={archive}
              disabled={busy}
            >
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
