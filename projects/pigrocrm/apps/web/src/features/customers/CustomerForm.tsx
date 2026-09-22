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
import type { ProblemDetail } from '@/lib/api'
import { clearedNativeValue, type FieldDefinition } from '@/lib/schema'
import type { Customer } from './queries'

const NATIVE_FIELDS: FieldDefinition[] = [
  { key: 'ragione_sociale', label: 'Ragione sociale', type: 'text', required: true, options: [] },
  { key: 'partita_iva', label: 'P.IVA', type: 'text', required: false, options: [] },
  { key: 'codice_fiscale', label: 'Codice fiscale', type: 'text', required: false, options: [] },
  { key: 'codice_sdi', label: 'Codice SDI', type: 'text', required: false, options: [] },
  { key: 'pec', label: 'PEC', type: 'text', required: false, options: [] },
  { key: 'indirizzo', label: 'Indirizzo', type: 'text', required: false, options: [] },
  { key: 'cap', label: 'CAP', type: 'text', required: false, options: [] },
  { key: 'comune', label: 'Comune', type: 'text', required: false, options: [] },
  { key: 'provincia', label: 'Provincia', type: 'text', required: false, options: [] },
  // Freeform text, not a `select`: CustomerCreate/Update place no enum on either
  // field (plain `SafeStr`, max_length only) -- offering a client-side dropdown of
  // guessed options would let the UI reject a value the backend, and an MCP agent
  // writing through the same service, both accept without complaint.
  { key: 'nazione', label: 'Nazione', type: 'text', required: false, options: [] },
  { key: 'email', label: 'Email', type: 'text', required: false, options: [] },
  { key: 'telefono', label: 'Telefono', type: 'text', required: false, options: [] },
  { key: 'sito_web', label: 'Sito web', type: 'url', required: false, options: [] },
  { key: 'stato', label: 'Stato', type: 'text', required: false, options: [] },
  // The payment terms (REB-326): what `InvoiceService.issue` computes every due date
  // to this customer from. Days empty means the fiscal profile's apply.
  { key: 'giorni_pagamento', label: 'Giorni di pagamento', type: 'number', required: false, options: [] },
  { key: 'pagamento_fine_mese', label: 'Scadenza a fine mese', type: 'checkbox', required: false, options: [] },
  { key: 'note', label: 'Note', type: 'textarea', required: false, options: [] },
]

const NATIVE_FIELD_KEYS = NATIVE_FIELDS.map((field) => field.key)

/**
 * The form's state, kept in the two namespaces the API itself uses -- native columns
 * at the top level of the request body, custom fields inside `custom_fields` -- and
 * never flattened into one.
 *
 * Flattening was a real, reproduced data bug, not a style question. A flat bag has to
 * re-derive, at submit time, which keys were custom, and the only thing available to
 * re-derive it from is the *currently active* schema. Archive a field definition
 * while a record still holds a value for it and that key drops out of the active
 * schema: the value is still in the form (it is still in the record), but it is now
 * classified as native and sent as a top-level key. `CustomerUpdate` is
 * `extra="forbid"`, so the PATCH 422s -- on an edit where the user changed nothing
 * -- and the record stays uneditable forever. Reproduced against the running API
 * before this fix, exactly that way.
 *
 * Provenance therefore has to be structural: a value that came out of
 * `customer.custom_fields` stays in `custom` for as long as the dialog is open, no
 * matter what happens to its definition in the meantime, and nothing ever re-asks the
 * schema what it is.
 */
export interface CustomerFormValues {
  /** Editable native columns, exactly `NATIVE_FIELD_KEYS`. */
  native: Record<string, unknown>
  /** Everything stored under `custom_fields`, including keys whose definition has
   *  since been archived and which therefore render nowhere. */
  custom: Record<string, unknown>
}

// `nazione` pre-filled to match `CustomerCreate.nazione`'s own server-side default
// (customers/schemas.py: `Field(default="IT", ...)`) -- a convenience, not a rule:
// leaving it blank omits the key from the payload entirely (see `submit` below) and
// the server fills in the exact same default either way.
const DEFAULT_CREATE_VALUES: CustomerFormValues = { native: { nazione: 'IT' }, custom: {} }

/**
 * Builds the form's starting state from a real, already-saved customer.
 *
 * Deliberately not `{ ...customer, ...customer.custom_fields }`: `customer` also
 * carries `id`, `created_at` and `updated_at`, and `CustomerUpdate` declares
 * `model_config = ConfigDict(extra="forbid")` (customers/schemas.py) -- sending
 * those three straight back would 422 with "extra fields not permitted" on every
 * single edit, since none of them is a field `CustomerUpdate` recognises. Listing
 * exactly the editable native keys keeps the round trip to only what the form is
 * actually allowed to send; `custom_fields` is copied whole into its own namespace,
 * archived keys included -- see `CustomerFormValues` for why they must not be
 * dropped and must not be merged in with the native ones.
 */
export function customerToFormValues(customer: Customer): CustomerFormValues {
  const native: Record<string, unknown> = {}
  for (const key of NATIVE_FIELD_KEYS) {
    native[key] = (customer as unknown as Record<string, unknown>)[key]
  }
  return { native, custom: { ...customer.custom_fields } }
}

/** Mirrors `is_blank` in packages/core/src/pigrocrm/core/fields/validator.py:
 *  `null`/`undefined`, a whitespace-only string, or an empty array all mean "no
 *  value" -- `false` and `0` do not, they are real values. `submit` below needs this
 *  same line drawn on the client for exactly the same reason `is_blank` is public
 *  and reused everywhere on the server: two different definitions of "empty" is how
 *  a required check and a clear-vs-untouched check start disagreeing. */
function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  return false
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  customFields: FieldDefinition[]
  initial?: CustomerFormValues
  problem?: ProblemDetail | null
  busy?: boolean
  onSubmit: (values: Record<string, unknown>) => void
  title: string
}

export function CustomerForm({
  open,
  onOpenChange,
  customFields,
  initial,
  problem,
  busy,
  onSubmit,
  title,
}: Props) {
  const [values, setValues] = useState<CustomerFormValues>(initial ?? DEFAULT_CREATE_VALUES)

  // This component stays mounted across opens -- only `Dialog`'s own visibility
  // toggles (see the list/detail routes: `open={open}` on an always-rendered
  // `<CustomerForm>`, not a conditional `{open && <CustomerForm />}`, which would
  // lose Radix's closing animation) -- so `useState`'s initializer alone, which
  // only ever runs on first mount, is not enough: reopening "Nuovo cliente" a
  // second time would still show the first customer's already-submitted values,
  // and reopening "Modifica" after the customer changed underneath it would still
  // show the stale copy. Comparing against the previous `open` value during render
  // and resetting synchronously is React's own documented pattern for this exact
  // case ("Adjusting state when a prop changes", react.dev) -- deliberately not a
  // `useEffect`: that would need `initial` in its dependency array to be honest
  // about what it reads, but the caller recomputes a brand-new `initial` object
  // every render (`customerToFormValues(customer)` has no stable identity), so the
  // effect would re-fire on every keystroke and wipe out whatever the user just
  // typed.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) setValues(initial ?? DEFAULT_CREATE_VALUES)
  }

  /** True for a key the active schema currently renders as a custom-field control.
   *
   *  This is only ever asked about a key the user can actually see and edit, which is
   *  exactly what the active schema describes -- it is *not* what decides whether a
   *  value is custom. That is `CustomerFormValues`' two namespaces, decided once when
   *  the form is seeded and never re-derived; see that interface's own docstring for
   *  the bug that re-deriving it caused. */
  const isRenderedCustomKey = (key: string) => customFields.some((field) => field.key === key)

  /** Routes an edit into the namespace the key belongs to. A key already present in
   *  `custom` stays custom even if the active schema no longer lists it -- provenance
   *  is never re-decided from a schema that can change underneath a stored value. */
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
      if (!isBlank(value)) {
        // A number control hands back its text (`DynamicFieldRenderer` reads
        // `event.target.value`); the column is an integer and `CustomerUpdate` is typed
        // `int | None`, so the digits are sent as the number they are.
        const type = NATIVE_FIELDS.find((field) => field.key === key)?.type
        native[key] = type === 'number' && typeof value === 'string' ? Number(value) : value
      } else if (!isBlank(initial?.native[key])) {
        // The user cleared a native column that used to hold a value -- say so
        // explicitly instead of dropping the key, or the old value survives
        // untouched. `clearedNativeValue` picks the spelling from the column's type;
        // every native field on this form is text-shaped, so it answers `""` for all
        // of them, exactly as before task 4B-1. The call stays anyway rather than a
        // hardcoded `""`: the day somebody adds a numeric column here, the wrong
        // spelling would be a 422 nobody would connect to this line.
        native[key] = clearedNativeValue(NATIVE_FIELDS, key)
      }
      // else: blank now, blank (or never set) before -- nothing changed, so there is
      // nothing to say.
    }

    const custom: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(values.custom)) {
      if (!isRenderedCustomKey(key)) {
        // A stored value whose definition is archived (or otherwise not in the
        // active schema): omit the key and the server carries the stored value over
        // untouched -- `_update_custom_fields` only ever touches keys the payload
        // mentions. This is the *only* correct thing to send. Verified all four
        // possibilities against the running API on this record's own shape:
        //   - as a top-level key (what a flattened form state produces): 422,
        //     `extra_forbidden` -- the bug this file's `CustomerFormValues` exists
        //     to make unrepresentable;
        //   - inside `custom_fields` with its stored value: 422, "campo non
        //     definito" -- `validate_custom_fields` refuses any key that is not an
        //     *active* definition, so echoing the value back is not an option;
        //   - inside `custom_fields` as `null`: accepted, and it deletes the value
        //     -- the opposite of what an untouched edit should do;
        //   - omitted: 200, and the stored value is still there afterwards.
        // The value stays in `values.custom` regardless, so it is never reclassified
        // as native and never mistaken for a field the user just cleared.
        continue
      }
      if (!isBlank(value)) {
        custom[key] = value
      } else if (!isBlank(initial?.custom[key])) {
        // A custom field clears only on an explicit `null` (`_update_custom_fields`
        // reads it as "remove this key"). `""` there is `is_blank`, which a
        // non-required field just quietly skips, leaving the stored value in place
        // -- so the two spellings are not interchangeable with the native one above.
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

        <DynamicForm
          fields={[...NATIVE_FIELDS, ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can
          // draw. The state behind it stays split (see `CustomerFormValues`), so what
          // reaches `submit` still knows which namespace each value came from. Custom
          // last, matching the field order below it: nothing today stops a
          // tenant-defined key from being named after a native column
          // (`FieldDefinitionService.create` only checks other definitions), and if
          // that ever happens the control the user sees and the value read back here
          // must at least be the same one.
          values={{ ...values.native, ...values.custom }}
          onChange={change}
          problem={problem}
          // `initial` is not a prefill, it *is* the record being edited
          // (`customerToFormValues(customer)`, from the detail route); its absence is
          // what "Nuovo cliente" means, and the list route passes none. Deriving the
          // mode from it rather than taking a third prop is what keeps the two from
          // ever disagreeing -- a `mode="edit"` next to no record, or a
          // `mode="create"` next to one, would be a contradiction this form could not
          // resolve. If a create dialog ever does get a prefilled `initial`, the
          // effect of guessing "edit" here is only that an untouched checkbox is
          // omitted rather than sent as `false`, which `renderFieldValue` already
          // reads as "No" anyway -- benign in the one direction it can be wrong.
          mode={initial === undefined ? 'create' : 'edit'}
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
