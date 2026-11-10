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
import { OrganizationPicker } from '@/features/organizations/OrganizationPicker'
import { EmailSyncNotice } from '@/features/gmail/EmailSyncNotice'
import { ContactPointsEditor } from '@/features/contact-points/ContactPointsEditor'
import {
  CONTACT_POINTS_KEY,
  isPointProblem,
  newEmailAddresses,
  pointErrors,
  pointsAreSendable,
  pointsForRequest,
  pointsToDrafts,
  type ContactPointDraft,
} from '@/features/contact-points/contactPoints'
import type { ProblemDetail } from '@/lib/api'
import { toFormValues, toRequestBody, type FormValues } from '@/lib/formValues'
import type { FieldDefinition } from '@/lib/schema'
import type { Person } from './queries'

// Email, phone and LinkedIn are not fields: they are the person's contact points, drawn by
// `ContactPointsEditor` and sent as `contact_points`.
const NATIVE_FIELDS: FieldDefinition[] = [
  { key: 'first_name', label: 'Nome', type: 'text', required: true, options: [] },
  { key: 'last_name', label: 'Cognome', type: 'text', required: false, options: [] },
  { key: 'role', label: 'Ruolo', type: 'text', required: false, options: [] },
]

/**
 * Create-only: on a new person `note` is its first note, signed and dated by whoever creates it
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

/**
 * Every top-level key `PersonRead`/`PersonUpdate` carry outside `custom_fields`,
 * `id`, `created_at` and `updated_at` -- one more than `NATIVE_FIELDS` itself
 * lists. `customer_id` is not rendered by `DynamicForm` (it has its own picker
 * below, not one of the nine `FieldDefinition` types), but it is still a native
 * column on the wire and needs the exact same structural provenance as the other
 * seven, or it would leak into `custom` the same way an unfiltered flat bag would
 * leak an archived key into `native` (see `PersonFormValues` below).
 * `contact_points` is not among them: the list is seeded by `personToFormValues` as
 * drafts and drawn by its own editor.
 */
const NATIVE_FIELD_KEYS = [...NATIVE_FIELDS.map((field) => field.key), 'customer_id']

/**
 * The form's state in the API's two namespaces -- see `FormValues`. `native` holds
 * `NATIVE_FIELD_KEYS`, `customer_id` included.
 */
export type PersonFormValues = FormValues

const DEFAULT_CREATE_VALUES: PersonFormValues = { native: {}, custom: {} }

/**
 * Builds the form's starting state from a real, already-saved person. A `PersonRead`
 * also carries `id`, `created_at` and `updated_at`, which `PersonUpdate`
 * (`extra="forbid"`) refuses -- see `toFormValues`.
 */
export function personToFormValues(person: Person): PersonFormValues {
  const values = toFormValues(person, NATIVE_FIELD_KEYS)
  values.native[CONTACT_POINTS_KEY] = pointsToDrafts(person.contact_points)
  return values
}

function draftsOf(values: PersonFormValues | undefined): ContactPointDraft[] {
  return (values?.native[CONTACT_POINTS_KEY] as ContactPointDraft[] | undefined) ?? []
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

  // Same "reset synchronously when `open` toggles" pattern as `OrganizationForm`,
  // for the identical reason: this component stays mounted across dialog opens
  // (`Dialog`'s own `open` prop toggles visibility, not this component's mount
  // state, or Radix's closing animation would break), so a plain
  // `useState(initial ?? {})` initializer would only ever run once and a second
  // "Nuova persona" would keep showing the first submission.
  const [wasOpen, setWasOpen] = useState(open)
  // Whether Salva was pressed: from then on the editor shows the browser's own row checks.
  const [checked, setChecked] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(initial ?? DEFAULT_CREATE_VALUES)
      setChecked(false)
    }
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

  const drafts = draftsOf(values)
  const initialDrafts = initial === undefined ? undefined : draftsOf(initial)
  // The list is checked and sent only when it differs from the one the form opened with: a
  // record whose stored values predate the rules must still save everything else.
  const pointsEdited = pointsForRequest(drafts, initialDrafts) !== undefined
  const errors = pointErrors(drafts, problem, checked && pointsEdited, initialDrafts)
  // Only for an address that is new to this record: see the notice below.
  const emailIsNew = newEmailAddresses(drafts, draftsOf(initial)).length > 0

  function submit() {
    if (pointsEdited && !pointsAreSendable(drafts, initialDrafts)) {
      setChecked(true)
      return
    }
    // `customer_id` cannot follow the plain-native-column rule: see the dedicated
    // handling below for why.
    const payload = toRequestBody(values, {
      fields: NATIVE_FIELDS,
      initial,
      isRenderedCustomKey,
      skipNative: (key) => key === 'customer_id' || key === CONTACT_POINTS_KEY,
    })

    // `customer_id` is a UUID column, not free text, and detaching it is not a
    // value of it. `""` would fail UUID parsing (422); `customer_id: null` never
    // reaches `PersonService.update`'s generic assignment at all -- the field is in
    // that method's `supplied_changes(exclude=...)` set, and stayed there when task
    // 4B-1 closed A14, precisely so the two spellings cannot disagree (read
    // people/schemas.py and people/service.py -- Organization has no equivalent
    // relationship to generalise this from). The wire schema instead carries a
    // dedicated `detach: bool` for exactly this, checked first in
    // `PersonService.update` and winning outright over any `customer_id` also
    // present in the same payload. Removing the customer of a person who
    // had one therefore has to send `detach: true`, never `customer_id: null`
    // and never an omitted key (which would just leave the old association in
    // place, indistinguishable from "the user changed nothing").
    const currentCustomerId = (values.native.customer_id as string | null | undefined) ?? null
    const initialCustomerId = (initial?.native.customer_id as string | null | undefined) ?? null
    if (currentCustomerId) {
      // Sent whenever set, whether or not it changed -- same idempotent-resend
      // pattern as every other native field; re-affirming the same
      // customer costs nothing beyond `PersonService._check_customer` confirming
      // it still exists.
      payload.customer_id = currentCustomerId
    } else if (initialCustomerId) {
      payload.detach = true
    }
    // else: no customer before, none now -- nothing to say, same as any other
    // untouched native field.

    // The whole list or nothing: a new person sends it when it has points, an edit only
    // when it changed (an omitted list leaves the stored one, `[]` clears it).
    const contactPoints = pointsForRequest(drafts, initialDrafts)
    if (contactPoints !== undefined) payload.contact_points = contactPoints

    onSubmit(payload)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          {/* «Azienda (cliente)» and not «Cliente»: what this picker sets is the person's
              company -- their "azienda di riferimento", the same thing the Persone list
              shows in its «Azienda» column -- and it is a *customer* record that holds
              it, which is why the parenthesis stays rather than the word being replaced.
              The value written is still `customer_id`; only the label changed. Rendered
              here, inside `DialogContent`: Radix mounts none of it while the dialog is
              closed, so the Persone list and every person's page do not search organizations
              for a picker nobody has opened. */}
          <Label htmlFor="customer">Azienda (cliente)</Label>
          <OrganizationPicker
            id="customer"
            value={(values.native.customer_id as string | null | undefined) ?? null}
            onChange={(customer) => change('customer_id', customer?.id ?? null)}
            placeholder="Nessun cliente: cerca per collegarne uno…"
          />
        </div>

        <DynamicForm
          fields={[...NATIVE_FIELDS, ...(initial === undefined ? [FIRST_NOTE_FIELD] : []), ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can
          // draw. The state behind it stays split (`PersonFormValues`), so what
          // reaches `submit` still knows which namespace each value came from.
          values={{ ...values.native, ...values.custom }}
          onChange={change}
          // The list's own sentences are drawn under its rows, not as a second banner here.
          problem={isPointProblem(problem) ? null : problem}
          // `initial`'s absence is what "Nuova persona" means; deriving `mode`
          // from it is what keeps the two from ever disagreeing. See
          // `DynamicForm`'s own docstring on this required prop.
          mode={initial === undefined ? 'create' : 'edit'}
        />

        <ContactPointsEditor
          points={drafts}
          onChange={(points) =>
            setValues((previous) => ({
              ...previous,
              native: { ...previous.native, [CONTACT_POINTS_KEY]: points },
            }))
          }
          errors={errors}
        />

        {/* Only for an address that is actually new to the CRM. An email already on
            this record is already in the roster and already being searched from the
            watermark, so repeating "at the next sync" on every edit would train the
            reader to ignore the one time it means something. `EmailSyncNotice` itself
            stays silent unless a cycle is really going to run -- see its docstring.
            Rendered here, inside `DialogContent`, for the same reason `OrganizationPicker`
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
