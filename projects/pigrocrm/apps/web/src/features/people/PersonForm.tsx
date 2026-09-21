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
import { EmailSyncNotice } from '@/features/gmail/EmailSyncNotice'
import type { ProblemDetail } from '@/lib/api'
import { clearedNativeValue, type FieldDefinition } from '@/lib/schema'
import type { Person } from './queries'

const NATIVE_FIELDS: FieldDefinition[] = [
  { key: 'nome', label: 'Nome', type: 'text', required: true, options: [] },
  { key: 'cognome', label: 'Cognome', type: 'text', required: false, options: [] },
  { key: 'ruolo', label: 'Ruolo', type: 'text', required: false, options: [] },
  { key: 'email', label: 'Email', type: 'text', required: false, options: [] },
  { key: 'telefono', label: 'Telefono', type: 'text', required: false, options: [] },
  { key: 'linkedin', label: 'LinkedIn', type: 'url', required: false, options: [] },
  { key: 'note', label: 'Note', type: 'textarea', required: false, options: [] },
]

/**
 * Every top-level key `PersonRead`/`PersonUpdate` carry outside `custom_fields`,
 * `id`, `created_at` and `updated_at` -- one more than `NATIVE_FIELDS` itself
 * lists. `customer_id` is not rendered by `DynamicForm` (it has its own picker
 * below, not one of the nine `FieldDefinition` types), but it is still a native
 * column on the wire and needs the exact same structural provenance as the other
 * seven, or it would leak into `custom` the same way an unfiltered flat bag would
 * leak an archived key into `native` (see `PersonFormValues` below).
 */
const NATIVE_FIELD_KEYS = [...NATIVE_FIELDS.map((field) => field.key), 'customer_id']

/**
 * Radix's `Select` refuses an item whose `value` is the empty string (see
 * `DynamicFieldRenderer.tsx`'s own `CLEAR_OPTION` for the identical constraint on
 * a custom `select` field), so "no customer" needs a sentinel string of its own.
 * Any string is safe here, unlike a tenant-defined option: a customer id is
 * always a UUID minted by the database (`Customer.id`), never user-entered text,
 * so no real id can ever equal this literal.
 */
const NO_CUSTOMER = '__nessuno__'

/**
 * The form's state, kept in the two namespaces the API itself uses -- native
 * columns at the top level of the request body, custom fields inside
 * `custom_fields` -- and never flattened into one. Copies
 * `features/customers/CustomerForm.tsx`'s `CustomerFormValues` exactly, for the
 * exact same reason: flattening was a real, reproduced data bug on Customers
 * (see that file's own docstring for the full reproduction), not a style
 * preference. Provenance has to be structural -- a value that came out of
 * `person.custom_fields` stays in `custom` for as long as the dialog is open, no
 * matter what happens to its definition in the meantime, and nothing ever
 * re-asks the schema what it is.
 */
export interface PersonFormValues {
  /** Editable native columns, exactly `NATIVE_FIELD_KEYS` (customer_id included). */
  native: Record<string, unknown>
  /** Everything stored under `custom_fields`, including keys whose definition has
   *  since been archived and which therefore render nowhere. */
  custom: Record<string, unknown>
}

const DEFAULT_CREATE_VALUES: PersonFormValues = { native: {}, custom: {} }

/**
 * Builds the form's starting state from a real, already-saved person.
 *
 * Deliberately not `{ ...person, ...person.custom_fields }`: `person` also
 * carries `id`, `created_at` and `updated_at`, and `PersonUpdate` declares
 * `model_config = ConfigDict(extra="forbid")` (people/schemas.py) -- sending
 * those three straight back would 422 with "extra fields not permitted" on
 * every single edit. Listing exactly the editable native keys keeps the round
 * trip to only what the form is actually allowed to send; `custom_fields` is
 * copied whole into its own namespace, archived keys included.
 */
export function personToFormValues(person: Person): PersonFormValues {
  const native: Record<string, unknown> = {}
  for (const key of NATIVE_FIELD_KEYS) {
    native[key] = (person as unknown as Record<string, unknown>)[key]
  }
  return { native, custom: { ...person.custom_fields } }
}

/** Mirrors `is_blank` in packages/core/src/pigrocrm/core/fields/validator.py, and
 *  `features/customers/CustomerForm.tsx`'s identical helper: `null`/`undefined`,
 *  a whitespace-only string, or an empty array all mean "no value" -- `false` and
 *  `0` do not, they are real values. */
function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  return false
}

/**
 * Split out of `PersonForm`, back-porting `DealForm.tsx`'s own `CustomerPicker`
 * fix: `useCustomers({limit: 200})` used to run at `PersonForm`'s own top level,
 * unconditionally, on every render of this component -- and `PersonForm` itself
 * stays mounted by its callers regardless of `open` (the `wasOpen` reset pattern
 * below only makes sense because of that), so every view of the Persone list and
 * every person detail page fired `GET /api/customers?limit=200` for a dropdown
 * nobody had opened there. Moving the hook into a child rendered here, inside
 * `DialogContent`, fixes it the same way: Radix does not render `DialogContent`'s
 * children at all while its `Dialog` is closed, so this only ever mounts -- and
 * only ever calls `useCustomers` -- while the dialog is actually open. Unlike
 * `DealForm`'s copy, this one is never additionally gated by an `isCreate` check:
 * a person's customer can be changed or detached on edit, not only chosen once at
 * create (see `submit`'s own `detach` handling below), so this picker belongs in
 * both modes, not create-only.
 */
function CustomerPicker({
  value,
  onChange,
}: {
  value: string | null
  onChange: (value: string | null) => void
}) {
  // `limit: 200` mirrors `DealForm`'s identical picker -- a one-shot cap for a
  // dropdown, not this screen's own list -- pagination for the Clienti list
  // itself is a known limitation of `routes/app/customers/index.tsx` (a single
  // unpaginated page, no further-pages control), not something this picker
  // needs to solve.
  const customers = useCustomers({ limit: 200 })

  return (
    <div className="space-y-2">
      {/* «Azienda (cliente)» and not «Cliente»: what this picker sets is the person's
          company -- their "azienda di riferimento", the same thing the Persone list now
          shows in its «Azienda» column -- and it is a *customer* record that holds it,
          which is why the parenthesis stays rather than the word being replaced. The
          value written is still `customer_id`; only the label changed. */}
      <Label htmlFor="customer">Azienda (cliente)</Label>
      <Select
        value={value ?? NO_CUSTOMER}
        onValueChange={(next) => onChange(next === NO_CUSTOMER ? null : next)}
      >
        <SelectTrigger id="customer" className="w-full">
          <SelectValue placeholder="Nessun cliente" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_CUSTOMER}>Nessun cliente</SelectItem>
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
  initial?: PersonFormValues
  problem?: ProblemDetail | null
  busy?: boolean
  onSubmit: (values: Record<string, unknown>) => void
  title: string
}

export function PersonForm({
  open,
  onOpenChange,
  customFields,
  initial,
  problem,
  busy,
  onSubmit,
  title,
}: Props) {
  const [values, setValues] = useState<PersonFormValues>(initial ?? DEFAULT_CREATE_VALUES)

  // Same "reset synchronously when `open` toggles" pattern as `CustomerForm`,
  // for the identical reason: this component stays mounted across dialog opens
  // (`Dialog`'s own `open` prop toggles visibility, not this component's mount
  // state, or Radix's closing animation would break), so a plain
  // `useState(initial ?? {})` initializer would only ever run once and a second
  // "Nuova persona" would keep showing the first submission.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) setValues(initial ?? DEFAULT_CREATE_VALUES)
  }

  /** True for a key the active schema currently renders as a custom-field
   *  control -- only ever asked to decide what the user currently sees, never to
   *  decide what a value *is*. See `PersonFormValues` for why provenance is
   *  never re-derived from this. */
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

  // `values.native.email` and not the flattened bag handed to `DynamicForm`: email is a
  // native column (`NATIVE_FIELDS`), and reading it out of the merged object would pick
  // up a custom field somebody happened to key `email` instead.
  const email = values.native.email
  const emailIsNew =
    typeof email === 'string' && email.trim() !== '' && email !== initial?.native.email

  function submit() {
    const native: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(values.native)) {
      // `customer_id` cannot follow the plain-native-column rule below: see the
      // dedicated handling just after this loop for why.
      if (key === 'customer_id') continue
      if (!isBlank(value)) {
        native[key] = value
      } else if (!isBlank(initial?.native[key])) {
        // The user cleared a native column that used to hold a value. The spelling
        // comes from the column's type (`clearedNativeValue`); every native field on
        // this form is text-shaped, so it is `""` for all of them, as before task
        // 4B-1 -- kept as a call so that a numeric column added here later cannot
        // inherit the wrong one.
        native[key] = clearedNativeValue(NATIVE_FIELDS, key)
      }
      // else: blank now, blank (or never set) before -- nothing to say.
    }

    // `customer_id` is a UUID column, not free text, and detaching it is not a
    // value of it. `""` would fail UUID parsing (422); `customer_id: null` never
    // reaches `PersonService.update`'s generic assignment at all -- the field is in
    // that method's `supplied_changes(exclude=...)` set, and stayed there when task
    // 4B-1 closed A14, precisely so the two spellings cannot disagree (read
    // people/schemas.py and people/service.py -- Customer has no equivalent
    // relationship to generalise this from). The wire schema instead carries a
    // dedicated `detach: bool` for exactly this, checked first in
    // `PersonService.update` and winning outright over any `customer_id` also
    // present in the same payload. Selecting "Nessun cliente" on a person who
    // had one therefore has to send `detach: true`, never `customer_id: null`
    // and never an omitted key (which would just leave the old association in
    // place, indistinguishable from "the user changed nothing").
    const currentCustomerId = (values.native.customer_id as string | null | undefined) ?? null
    const initialCustomerId = (initial?.native.customer_id as string | null | undefined) ?? null
    let detach = false
    if (currentCustomerId) {
      // Sent whenever set, whether or not it changed -- same idempotent-resend
      // pattern as every other native field above; re-affirming the same
      // customer costs nothing beyond `PersonService._check_customer` confirming
      // it still exists.
      native.customer_id = currentCustomerId
    } else if (initialCustomerId) {
      detach = true
    }
    // else: no customer before, none now -- nothing to say, same as any other
    // untouched native field.

    const custom: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(values.custom)) {
      if (!isRenderedCustomKey(key)) {
        // A stored value whose definition is archived: omit the key and
        // `_update_custom_fields` carries the stored value over untouched --
        // see `CustomerForm.submit`'s docstring for the four spellings this was
        // verified against on Customers; `PersonService._update_custom_fields`
        // copies that same contract exactly (people/service.py's own docstring
        // says so).
        continue
      }
      if (!isBlank(value)) {
        custom[key] = value
      } else if (!isBlank(initial?.custom[key])) {
        // A custom field clears only on an explicit `null`.
        custom[key] = null
      }
    }

    const payload: Record<string, unknown> = { ...native, custom_fields: custom }
    if (detach) payload.detach = true
    onSubmit(payload)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <CustomerPicker
          value={(values.native.customer_id as string | null) ?? null}
          onChange={(value) => change('customer_id', value)}
        />

        <DynamicForm
          fields={[...NATIVE_FIELDS, ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can
          // draw. The state behind it stays split (`PersonFormValues`), so what
          // reaches `submit` still knows which namespace each value came from.
          values={{ ...values.native, ...values.custom }}
          onChange={change}
          problem={problem}
          // `initial`'s absence is what "Nuova persona" means; deriving `mode`
          // from it is what keeps the two from ever disagreeing. See
          // `DynamicForm`'s own docstring on this required prop.
          mode={initial === undefined ? 'create' : 'edit'}
        />

        {/* Only for an address that is actually new to the CRM. An email already on
            this record is already in the roster and already being searched from the
            watermark, so repeating "at the next sync" on every edit would train the
            reader to ignore the one time it means something. `EmailSyncNotice` itself
            stays silent unless a cycle is really going to run -- see its docstring.
            Rendered here, inside `DialogContent`, for the same reason `CustomerPicker`
            is: Radix mounts none of these children while the dialog is closed, so the
            health query is not read by every Persone list page. */}
        {emailIsNew ? <EmailSyncNotice /> : null}

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
