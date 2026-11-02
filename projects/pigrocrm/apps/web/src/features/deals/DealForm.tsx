import { useState } from 'react'
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
import { CustomerPicker } from '@/features/customers/CustomerPicker'
import type { ProblemDetail } from '@/lib/api'
import { toFormValues, toRequestBody, type FormValues } from '@/lib/formValues'
import type { FieldDefinition } from '@/lib/schema'
import type { Deal } from './queries'

const NATIVE_FIELDS: FieldDefinition[] = [
  { key: 'nome', label: 'Nome', type: 'text', required: true, options: [] },
  { key: 'valore_previsto', label: 'Valore previsto', type: 'currency', required: false, options: [] },
  { key: 'probabilita', label: 'Probabilità (%)', type: 'number', required: false, options: [] },
  {
    key: 'data_chiusura_prevista',
    label: 'Chiusura prevista',
    type: 'date',
    required: false,
    options: [],
  },
  { key: 'ore_preventivate', label: 'Ore preventivate', type: 'number', required: false, options: [] },
  {
    key: 'valore_preventivato',
    label: 'Valore preventivato',
    type: 'currency',
    required: false,
    options: [],
  },
]

/**
 * Create-only: on a new deal `note` is its first note, signed and dated by whoever creates it
 * (REB-879). Once it exists the notes are a thread of their own on the record's page, and
 * the update schema refuses `note` outright, so an edit never draws this field.
 */
const FIRST_NOTE_FIELD: FieldDefinition = {
  key: 'note',
  label: 'Nota iniziale',
  type: 'textarea',
  required: false,
  options: [],
}
// `owner_id` (REB-780) is the same kind of key as `customer_id`: an id drawn by its own
// picker below `DynamicForm`, never a `FieldDefinition` (a `select` field would submit the
// option's label). It is seeded by `dealToFormValues` apart from `NATIVE_FIELD_KEYS`.

/** What the owner picker's «nessuno» option submits as: a `Select` item cannot hold `null`. */
const NO_OWNER = '__nessuno'

/** The people a deal may be handed to: the space's active members (REB-780). */
export interface OwnerOption {
  id: string
  nome: string
}

const NATIVE_FIELD_KEYS = NATIVE_FIELDS.map((field) => field.key)

/**
 * The form's state in the API's two namespaces -- see `FormValues`.
 *
 * `customer_id` is deliberately **not** one of `NATIVE_FIELD_KEYS`, unlike
 * Person's identically-shaped values. `DealUpdate` has no `customer_id` field at
 * all -- deals/schemas.py's own comment: reassigning it "would bypass the
 * validation ... logic that create exists to enforce", and unlike
 * `pipeline_stage_id` (which has `move_stage` as its dedicated, supported way to
 * change), there is no re-assign endpoint for a deal's customer at all. A deal's
 * customer is fixed at creation and never shown as editable again. The "Cliente"
 * picker below still writes into `native.customer_id` through the same generic
 * `change()` every other field uses -- it is simply never seeded by
 * `dealToFormValues` (there is nothing to show; the picker itself is only
 * rendered on create) and never part of an edit payload (`submit` below only
 * ever reads it back out when `initial` is `undefined`).
 */
export type DealFormValues = FormValues

const DEFAULT_CREATE_VALUES: DealFormValues = { native: {}, custom: {} }

/**
 * Builds the form's starting state from a real, already-saved deal.
 *
 * A `DealRead` also carries `id`, `customer_id`, `pipeline_stage_id`, `created_at` and
 * `updated_at`, none of which `DealUpdate` accepts (`extra="forbid"`), so only
 * `NATIVE_FIELD_KEYS` and the owner are seeded -- see `toFormValues`.
 */
export function dealToFormValues(deal: Deal): DealFormValues {
  const values = toFormValues(deal, NATIVE_FIELD_KEYS)
  values.native.owner_id = deal.owner_id
  return values
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  customFields: FieldDefinition[]
  initial?: DealFormValues
  problem?: ProblemDetail | null
  busy?: boolean
  onSubmit: (values: Record<string, unknown>) => void
  title: string
  /** The owner picker's people: the space's active members. Without it no control is
   *  drawn and no `owner_id` is sent. */
  owners?: OwnerOption[]
  /** Who a new deal belongs to when the picker is left alone: the signed-in person, which
   *  is what the server does with an omitted `owner_id` (REB-780). */
  defaultOwnerId?: string | null
}

export function DealForm({
  open,
  onOpenChange,
  customFields,
  initial,
  problem,
  busy,
  onSubmit,
  title,
  owners,
  defaultOwnerId,
}: Props) {
  const [values, setValues] = useState<DealFormValues>(initial ?? DEFAULT_CREATE_VALUES)
  // `initial`'s absence is what "Nuovo deal" means, exactly like `CustomerForm`/
  // `PersonForm` -- derived rather than a separate boolean prop, so the "show the
  // Cliente picker" decision and the "which DynamicForm mode" decision can never
  // disagree with each other or with what `initial` actually says.
  const isCreate = initial === undefined
  // `undefined`: the picker was not touched, so a create shows the default and sends nothing.
  const chosenOwner = values.native.owner_id as string | null | undefined
  const shownOwner = chosenOwner === undefined && isCreate ? (defaultOwnerId ?? null) : chosenOwner

  // Same "reset synchronously when `open` toggles" pattern as `CustomerForm`/
  // `PersonForm`, for the identical reason: this component stays mounted across
  // dialog opens (`Dialog`'s own `open` prop toggles visibility, not this
  // component's mount state), so a plain `useState(initial ?? {})` initializer
  // would only ever run once.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) setValues(initial ?? DEFAULT_CREATE_VALUES)
  }

  /** True for a key the active schema currently renders as a custom-field
   *  control -- only ever asked to decide what the user currently sees, never to
   *  decide what a value *is*. See `DealFormValues` for why provenance is never
   *  re-derived from this. */
  const isRenderedCustomKey = (key: string) => customFields.some((field) => field.key === key)

  /** Routes an edit into the namespace the key belongs to. A key already present
   *  in `custom` stays custom even if the active schema no longer lists it. */
  function change(key: string, value: unknown) {
    setValues((previous) =>
      isRenderedCustomKey(key) || key in previous.custom
        ? { ...previous, custom: { ...previous.custom, [key]: value } }
        : { ...previous, native: { ...previous.native, [key]: value } },
    )
  }

  function submit() {
    // `nome` clears on `""`, while `valore_previsto`, `ore_preventivate`,
    // `valore_preventivato` and `data_chiusura_prevista` clear on `null` -- sending `""`
    // for those four was a 422 from Pydantic: an estimate typed once could not be taken
    // back. `customer_id` and `owner_id` are handled below instead.
    const body = toRequestBody(values, {
      fields: NATIVE_FIELDS,
      initial,
      isRenderedCustomKey,
      skipNative: (key) => key === 'customer_id' || key === 'owner_id',
    })

    // Create-only: see `DealFormValues`'s own docstring for why `customer_id`
    // never appears in an edit payload at all -- the picker is not even rendered
    // then. Sent whenever chosen; left out entirely when not.
    // `DealCreate.customer_id` has no default, so an omitted key surfaces the
    // backend's own "field required" on this exact control the same way every
    // other required-but-empty field on this form already does (see
    // `DynamicForm`'s `unattributedMessage`: a problem naming a field this form
    // does not render still reaches the user as a banner) -- no client-side gate
    // duplicates that check here.
    if (isCreate) {
      const customerId = values.native.customer_id as string | null | undefined
      if (customerId) body.customer_id = customerId
    }

    // Only what was changed: re-sending an unchanged owner would be refused the day that
    // person is deactivated. `null` takes the owner away.
    if (owners !== undefined && chosenOwner !== undefined && chosenOwner !== initial?.native.owner_id) {
      body.owner_id = chosenOwner
    }

    onSubmit(body)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        {isCreate && (
          <div className="space-y-2">
            <Label htmlFor="deal-customer">
              Cliente
              {/* The semantic `destructive` token, matching DynamicFieldRenderer.
                  tsx's own `RequiredMark` -- never the raw brand `--color-
                  watermelon`, which that component's docstring reserves for
                  logos/decorative accents, not small body text like this. */}
              <span className="ml-1 text-destructive">*</span>
            </Label>
            <CustomerPicker
              id="deal-customer"
              value={(values.native.customer_id as string | null | undefined) ?? null}
              onChange={(customer) => change('customer_id', customer?.id ?? null)}
            />
          </div>
        )}

        <DynamicForm
          fields={[...NATIVE_FIELDS, ...(isCreate ? [FIRST_NOTE_FIELD] : []), ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can
          // draw. The state behind it stays split (`DealFormValues`), so what
          // reaches `submit` still knows which namespace each value came from.
          values={{ ...values.native, ...values.custom }}
          onChange={change}
          problem={problem}
          mode={isCreate ? 'create' : 'edit'}
        />

        {owners !== undefined ? (
          <div className="space-y-2">
            <Label htmlFor="deal-owner">Responsabile</Label>
            <Select
              value={shownOwner ?? NO_OWNER}
              onValueChange={(value) =>
                setValues((previous) => ({
                  ...previous,
                  native: { ...previous.native, owner_id: value === NO_OWNER ? null : value },
                }))
              }
            >
              <SelectTrigger id="deal-owner" className="w-full">
                <SelectValue placeholder="Seleziona…" />
              </SelectTrigger>
              <SelectContent>
                {/* A new deal always has an owner: an omitted one is the creator, so
                    «nessuno» can only be said on an existing deal. */}
                {!isCreate && <SelectItem value={NO_OWNER}>Nessuno</SelectItem>}
                {owners.map((person) => (
                  <SelectItem key={person.id} value={person.id}>
                    {person.nome}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">
              Chi riceve i promemoria su questo deal.
            </p>
          </div>
        ) : null}

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
