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
import { useCustomers } from '@/features/customers/queries'
import type { ProblemDetail } from '@/lib/api'
import { clearedNativeValue, type FieldDefinition } from '@/lib/schema'
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
  { key: 'note', label: 'Note', type: 'textarea', required: false, options: [] },
]
// `owner_id` -- `DealCreate`/`DealUpdate`'s other native column -- is deliberately
// absent from this list: it is a foreign key to `users`, needing a user picker no
// `FieldDefinition` type covers (the nine types are text/textarea/number/
// currency/date/select/multiselect/checkbox/url -- lib/schema.ts's own
// `FieldType`). `features/settings/queries.ts` has `useUsers` for Impostazioni's
// own Utenti panel, but no owner-picker control built from it exists yet;
// assigning an owner is left to whichever later slice adds one, the same way
// `ore_preventivate`/`valore_preventivato` are written now but not read until
// slice 4.

const NATIVE_FIELD_KEYS = NATIVE_FIELDS.map((field) => field.key)

/**
 * The form's state, kept in the two namespaces the API itself uses -- copies
 * `features/customers/CustomerForm.tsx`'s `CustomerFormValues` exactly, for the
 * exact same reason (see that file's own docstring for the full reproduction of
 * the bug a flattened bag caused on Customers, closed the same way on Persons):
 * provenance has to be structural, decided once when the form is seeded, never
 * re-derived from whatever the active schema currently renders.
 *
 * `customer_id` is deliberately **not** one of `NATIVE_FIELD_KEYS`, unlike
 * Person's identically-shaped `PersonFormValues`. `DealUpdate` has no
 * `customer_id` field at all -- deals/schemas.py's own comment: reassigning it
 * "would bypass the validation ... logic that create exists to enforce", and
 * unlike `pipeline_stage_id` (which has `move_stage` as its dedicated, supported
 * way to change), there is no re-assign endpoint for a deal's customer at all. A
 * deal's customer is fixed at creation and never shown as editable again. The
 * "Cliente" picker below still writes into `native.customer_id` through the same
 * generic `change()` every other field uses -- it is simply never seeded by
 * `dealToFormValues` (there is nothing to show; the picker itself is only
 * rendered on create) and never part of an edit payload (`submit` below only
 * ever reads it back out when `initial` is `undefined`).
 */
export interface DealFormValues {
  /** Editable native columns, exactly `NATIVE_FIELD_KEYS`, plus `customer_id`
   *  while a create dialog is open (see this interface's own docstring). */
  native: Record<string, unknown>
  /** Everything stored under `custom_fields`, including keys whose definition
   *  has since been archived and which therefore render nowhere. */
  custom: Record<string, unknown>
}

const DEFAULT_CREATE_VALUES: DealFormValues = { native: {}, custom: {} }

/**
 * Builds the form's starting state from a real, already-saved deal.
 *
 * Deliberately not `{ ...deal, ...deal.custom_fields }`: `deal` also carries
 * `id`, `customer_id`, `pipeline_stage_id`, `owner_id`, `created_at` and
 * `updated_at`, none of which `DealUpdate` accepts (`model_config =
 * ConfigDict(extra="forbid")`, deals/schemas.py) -- sending any of them straight
 * back would 422 with "extra fields not permitted" on every single edit.
 * Listing exactly the editable native keys keeps the round trip to only what the
 * form is actually allowed to send; `custom_fields` is copied whole into its own
 * namespace, archived keys included -- see `DealFormValues` for why they must
 * not be dropped and must not be merged in with the native ones.
 */
export function dealToFormValues(deal: Deal): DealFormValues {
  const native: Record<string, unknown> = {}
  for (const key of NATIVE_FIELD_KEYS) {
    native[key] = (deal as unknown as Record<string, unknown>)[key]
  }
  return { native, custom: { ...deal.custom_fields } }
}

/** Mirrors `is_blank` in packages/core/src/pigrocrm/core/fields/validator.py, and
 *  `CustomerForm`/`PersonForm`'s identical helper: `null`/`undefined`, a
 *  whitespace-only string, or an empty array all mean "no value" -- `false` and
 *  `0` do not, they are real values. */
function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  return false
}

/**
 * Split out of `DealForm` so `useCustomers` is only ever mounted -- and
 * therefore only ever called -- while this picker is actually rendered, i.e.
 * only on create. `DealForm` stays mounted across every dialog open/close
 * (see the `wasOpen` reset pattern below) and, on the detail route, sits
 * behind `open={false}` for as long as the page is simply being *viewed* --
 * before this split, `isCreate` gated only the JSX, so `useCustomers({limit:
 * 200})` still ran unconditionally and fired a `GET /api/customers?limit=200`
 * on every single deal detail page view, for a dropdown nobody could see or
 * use there. Conditionally *rendering* this component, rather than
 * conditionally *calling* the hook inside the parent, is the same fix
 * `features/people/$personId.tsx`'s `LinkedCustomerRow` applies for a
 * different reason (an unsafe empty id there; a wasted request here) --
 * `DealForm` itself needs no change beyond mounting this only inside
 * `{isCreate && ...}`.
 */
function CustomerPicker({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  // `limit: 200` mirrors `PersonForm`'s identical picker -- a one-shot cap for
  // a dropdown, not a paginated list of its own; see that file's own comment.
  const customers = useCustomers({ limit: 200 })

  return (
    <div className="space-y-2">
      <Label htmlFor="deal-customer">
        Cliente
        {/* The semantic `destructive` token, matching DynamicFieldRenderer.
            tsx's own `RequiredMark` -- never the raw brand `--color-
            watermelon`, which that component's docstring reserves for
            logos/decorative accents, not small body text like this. */}
        <span className="ml-1 text-destructive">*</span>
      </Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id="deal-customer" className="w-full">
          <SelectValue placeholder="Seleziona un cliente…" />
        </SelectTrigger>
        <SelectContent>
          {customers.data?.items.map((customer) => (
            <SelectItem key={customer.id} value={customer.id}>
              {customer.ragione_sociale}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
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
}: Props) {
  const [values, setValues] = useState<DealFormValues>(initial ?? DEFAULT_CREATE_VALUES)
  // `initial`'s absence is what "Nuovo deal" means, exactly like `CustomerForm`/
  // `PersonForm` -- derived rather than a separate boolean prop, so the "show the
  // Cliente picker" decision and the "which DynamicForm mode" decision can never
  // disagree with each other or with what `initial` actually says.
  const isCreate = initial === undefined

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
    const native: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(values.native)) {
      // `customer_id` cannot follow the plain-native-column rule below -- see the
      // dedicated handling just after this loop for why.
      if (key === 'customer_id') continue
      if (!isBlank(value)) {
        native[key] = value
      } else if (!isBlank(initial?.native[key])) {
        // The user cleared a native column that used to hold a value -- say so
        // explicitly instead of dropping the key, or the old value survives
        // untouched. Which spelling says it depends on the column's type, and this
        // form is the one that has both: `nome` and `note` clear on `""`, while
        // `valore_previsto`, `ore_preventivate`, `valore_preventivato` and
        // `data_chiusura_prevista` clear on `null`. Sending `""` for those four was
        // a 422 from Pydantic before it was anything else -- an estimate typed once
        // could not be taken back, which is residual A14 seen from this side of the
        // wire. See `clearedNativeValue`.
        native[key] = clearedNativeValue(NATIVE_FIELDS, key)
      }
      // else: blank now, blank (or never set) before -- nothing changed.
    }

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
      if (customerId) native.customer_id = customerId
    }

    const custom: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(values.custom)) {
      if (!isRenderedCustomKey(key)) {
        // A stored value whose definition is archived: omit the key and
        // `_update_custom_fields` carries the stored value over untouched -- see
        // `CustomerForm.submit`'s docstring for the four spellings this was
        // verified against on Customers; `DealService._update_custom_fields`
        // copies that same contract exactly.
        continue
      }
      if (!isBlank(value)) {
        custom[key] = value
      } else if (!isBlank(initial?.custom[key])) {
        // A custom field clears only on an explicit `null`.
        custom[key] = null
      }
    }

    onSubmit({ ...native, custom_fields: custom })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        {isCreate && (
          <CustomerPicker
            value={(values.native.customer_id as string) ?? ''}
            onChange={(value) => change('customer_id', value)}
          />
        )}

        <DynamicForm
          fields={[...NATIVE_FIELDS, ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can
          // draw. The state behind it stays split (`DealFormValues`), so what
          // reaches `submit` still knows which namespace each value came from.
          values={{ ...values.native, ...values.custom }}
          onChange={change}
          problem={problem}
          mode={isCreate ? 'create' : 'edit'}
        />

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
