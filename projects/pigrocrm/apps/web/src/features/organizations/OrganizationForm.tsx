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
import { fieldErrorFrom, type ProblemDetail } from '@/lib/api'
import { useLegalEntity, useLegalEntityProposal } from '@/lib/legalEntity'
import { isBlank, toFormValues, toRequestBody, type FormValues } from '@/lib/formValues'
import type { FieldDefinition } from '@/lib/schema'
import { ContactPointsEditor } from '@/features/contact-points/ContactPointsEditor'
import {
  CONTACT_POINTS_KEY,
  isPointProblem,
  pointErrors,
  pointsAreSendable,
  pointsForRequest,
  pointsToDrafts,
  type ContactPointDraft,
} from '@/features/contact-points/contactPoints'
import type { Organization } from './queries'

const NATIVE_FIELDS: FieldDefinition[] = [
  { key: 'legal_name', label: 'Ragione sociale', type: 'text', required: true, options: [] },
  { key: 'vat_number', label: 'P.IVA', type: 'text', required: false, options: [] },
  { key: 'tax_code', label: 'Codice fiscale', type: 'text', required: false, options: [] },
  { key: 'sdi_code', label: 'Codice SDI', type: 'text', required: false, options: [] },
  { key: 'certified_email', label: 'PEC', type: 'text', required: false, options: [] },
  { key: 'address', label: 'Indirizzo', type: 'text', required: false, options: [] },
  { key: 'postal_code', label: 'CAP', type: 'text', required: false, options: [] },
  { key: 'city', label: 'Comune', type: 'text', required: false, options: [] },
  { key: 'province', label: 'Provincia', type: 'text', required: false, options: [] },
  // Freeform text, not a `select`: OrganizationCreate/Update place no enum on either
  // field (plain `SafeStr`, max_length only) -- offering a client-side dropdown of
  // guessed options would let the UI reject a value the backend, and an MCP agent
  // writing through the same service, both accept without complaint.
  { key: 'country', label: 'Nazione', type: 'text', required: false, options: [] },
  { key: 'website', label: 'Sito web', type: 'url', required: false, options: [] },
  { key: 'status', label: 'Stato', type: 'text', required: false, options: [] },
  // The payment terms (REB-326): what `InvoiceService.issue` computes every due date
  // to this customer from. Days empty means the fiscal profile's apply.
  { key: 'payment_days', label: 'Giorni di pagamento', type: 'number', required: false, options: [] },
  { key: 'payment_end_of_month', label: 'Scadenza a fine mese', type: 'checkbox', required: false, options: [] },
]

/**
 * Create-only: on a new customer `note` is its first note, signed and dated by whoever creates it
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

const NATIVE_FIELD_KEYS = NATIVE_FIELDS.map((field) => field.key)

// `legal_entity_id` is seeded and sent like a native column but drawn by its own picker below
// `DynamicForm`, the way the cost form draws `category_id`: the value is an id, and a
// `select` field would submit the option's label (REB-626).
// `owner_id` (REB-773) is the same kind of key: an id drawn by its own picker, only for the
// admin who may change it.
const SEEDED_KEYS = [...NATIVE_FIELD_KEYS, 'legal_entity_id', 'owner_id']

/** What the owner picker's «nessuno» option submits as: a `Select` item cannot hold `null`. */
const NO_OWNER = '__nessuno'

/** The people an admin may hand a customer to (REB-773), as the detail page draws them. */
export interface OwnerOption {
  id: string
  name: string
}

/**
 * The form's state in the API's two namespaces -- see `FormValues` for why it is never
 * flattened. `native` holds `SEEDED_KEYS`; `custom` everything stored under
 * `custom_fields`, archived keys included.
 */
export type OrganizationFormValues = FormValues

// `country` pre-filled to match `OrganizationCreate.country`'s own server-side default
// (organizations/schemas.py: `Field(default="IT", ...)`) -- a convenience, not a rule:
// leaving it blank omits the key from the payload entirely (see `submit` below) and
// the server fills in the exact same default either way.
const DEFAULT_CREATE_VALUES: OrganizationFormValues = { native: { country: 'IT' }, custom: {} }

/**
 * Builds the form's starting state from a real, already-saved customer.
 *
 * A `OrganizationRead` also carries `id`, `created_at` and `updated_at`, and
 * `OrganizationUpdate` is `extra="forbid"` (organizations/schemas.py), so sending them back would
 * 422 on every edit: only `SEEDED_KEYS` are seeded -- see `toFormValues`.
 */
export function organizationToFormValues(customer: Organization): OrganizationFormValues {
  const values = toFormValues(customer, SEEDED_KEYS)
  values.native[CONTACT_POINTS_KEY] = pointsToDrafts(customer.contact_points)
  return values
}

function draftsOf(values: OrganizationFormValues | undefined): ContactPointDraft[] {
  return (values?.native[CONTACT_POINTS_KEY] as ContactPointDraft[] | undefined) ?? []
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  customFields: FieldDefinition[]
  initial?: OrganizationFormValues
  problem?: ProblemDetail | null
  busy?: boolean
  onSubmit: (values: Record<string, unknown>) => void
  title: string
  /** The owner picker's people. Given only to someone who may change the owner (an
   *  admin, REB-773): without it no control is drawn and no `owner_id` is sent. */
  owners?: OwnerOption[]
}

export function OrganizationForm({
  open,
  onOpenChange,
  customFields,
  initial,
  problem,
  busy,
  onSubmit,
  title,
  owners,
}: Props) {
  const [values, setValues] = useState<OrganizationFormValues>(initial ?? DEFAULT_CREATE_VALUES)
  const isCreate = initial === undefined
  // «Azienda» from the second legal entity on (REB-626, spec §1.6): while creating, the one
  // the nation proposes is shown live until the person picks one by hand, and what the
  // form shows is what is sent, so the saved customer carries it. While editing the
  // stored one is shown and sent only if changed.
  const legal_entity = useLegalEntity()
  const chosenLegalEntity = values.native.legal_entity_id as string | null | undefined
  const country = typeof values.native.country === 'string' ? values.native.country : ''
  const proposal = useLegalEntityProposal(
    country,
    legal_entity.several && isCreate && (chosenLegalEntity === undefined || chosenLegalEntity === null),
  )
  const shownLegalEntity = chosenLegalEntity ?? (isCreate ? (proposal.data?.id ?? '') : '')
  // A customer whose legal entity has since been deactivated is stranded: nothing new can
  // be created under it until it moves (spec §3), so the picker is drawn for it even
  // when one active legal entity is left and the selector elsewhere has gone (Greptile, PR
  // #509). It then offers the active legal entities and shows no current value, since the
  // deactivated one is not among them.
  const initialLegalEntity = initial?.native.legal_entity_id
  const stranded =
    !isCreate &&
    typeof initialLegalEntity === 'string' &&
    legal_entity.legal_entities.length > 0 &&
    legal_entity.byId(initialLegalEntity) === undefined
  const showLegalEntityPicker = legal_entity.several || stranded
  // `legal_entity_id` has its own picker below and no control in `DynamicForm`, so it is kept
  // out of the flattened values: a tenant's custom field of the same name would
  // otherwise display the chosen legal entity's id as if it were its own value (Greptile,
  // PR #509).
  const { legal_entity_id: _pickedElsewhere, owner_id: _ownerElsewhere, ...nativeForForm } = values.native
  void _pickedElsewhere
  void _ownerElsewhere
  const chosenOwner = values.native.owner_id as string | null | undefined

  // This component stays mounted across opens -- only `Dialog`'s own visibility
  // toggles (see the list/detail routes: `open={open}` on an always-rendered
  // `<OrganizationForm>`, not a conditional `{open && <OrganizationForm />}`, which would
  // lose Radix's closing animation) -- so `useState`'s initializer alone, which
  // only ever runs on first mount, is not enough: reopening "Nuovo cliente" a
  // second time would still show the first customer's already-submitted values,
  // and reopening "Modifica" after the customer changed underneath it would still
  // show the stale copy. Comparing against the previous `open` value during render
  // and resetting synchronously is React's own documented pattern for this exact
  // case ("Adjusting state when a prop changes", react.dev) -- deliberately not a
  // `useEffect`: that would need `initial` in its dependency array to be honest
  // about what it reads, but the caller recomputes a brand-new `initial` object
  // every render (`organizationToFormValues(customer)` has no stable identity), so the
  // effect would re-fire on every keystroke and wipe out whatever the user just
  // typed.
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

  const drafts = draftsOf(values)
  const initialDrafts = isCreate ? undefined : draftsOf(initial)
  // The list is checked and sent only when it differs from the one the form opened with: a
  // record whose stored values predate the rules must still save everything else.
  const pointsEdited = pointsForRequest(drafts, initialDrafts) !== undefined
  const pointRows = pointErrors(drafts, problem, checked && pointsEdited, initialDrafts)

  /** True for a key the active schema currently renders as a custom-field control.
   *
   *  This is only ever asked about a key the user can actually see and edit, which is
   *  exactly what the active schema describes -- it is *not* what decides whether a
   *  value is custom. That is `FormValues`' two namespaces, decided once when the form
   *  is seeded and never re-derived; see that interface's own docstring for the bug
   *  that re-deriving it caused. */
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
    if (pointsEdited && !pointsAreSendable(drafts, initialDrafts)) {
      setChecked(true)
      return
    }
    // `legal_entity_id` and `owner_id` are decided below from what their pickers show, not
    // from the seeded value: re-sending an unchanged id would be refused the day that
    // legal entity is deactivated.
    const body = toRequestBody(values, {
      fields: NATIVE_FIELDS,
      initial,
      isRenderedCustomKey,
      skipNative: (key) =>
        key === 'legal_entity_id' || key === 'owner_id' || key === CONTACT_POINTS_KEY,
      // A number control hands back its text (`DynamicFieldRenderer` reads
      // `event.target.value`); the column is an integer and `OrganizationUpdate` is typed
      // `int | None`, so the digits are sent as the number they are.
      coerceNative: (key, value) =>
        NATIVE_FIELDS.find((field) => field.key === key)?.type === 'number' &&
        typeof value === 'string'
          ? Number(value)
          : value,
    })

    if (showLegalEntityPicker) {
      if (isCreate && shownLegalEntity !== '') body.legal_entity_id = shownLegalEntity
      if (!isCreate && !isBlank(chosenLegalEntity) && chosenLegalEntity !== initial?.native.legal_entity_id) {
        body.legal_entity_id = chosenLegalEntity
      }
    }

    // Only what the admin changed: re-sending an unchanged owner would be refused the
    // day that person is deactivated.
    if (owners !== undefined && chosenOwner !== undefined && chosenOwner !== initial?.native.owner_id) {
      body.owner_id = chosenOwner
    }

    // The whole list or nothing: a new customer sends it when it has points, an edit only
    // when it changed (an omitted list leaves the stored one, `[]` clears it).
    const contactPoints = pointsForRequest(drafts, initialDrafts)
    if (contactPoints !== undefined) body.contact_points = contactPoints

    onSubmit(body)
  }

  const legalEntityError = problem ? fieldErrorFrom(problem) : null
  const legalEntityRefused = legalEntityError?.field === 'legal_entity_id' ? legalEntityError.message : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <DynamicForm
          fields={[...NATIVE_FIELDS, ...(isCreate ? [FIRST_NOTE_FIELD] : []), ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can
          // draw. The state behind it stays split (see `OrganizationFormValues`), so what
          // reaches `submit` still knows which namespace each value came from. Custom
          // last, matching the field order below it: nothing today stops a
          // tenant-defined key from being named after a native column
          // (`FieldDefinitionService.create` only checks other definitions), and if
          // that ever happens the control the user sees and the value read back here
          // must at least be the same one.
          values={{ ...nativeForForm, ...values.custom }}
          onChange={change}
          // Withheld when the server blamed `legal_entity_id`: that key has no control inside
          // `DynamicForm`, so it would render the message a second time as a raw banner
          // next to the line shown under the picker below.
          problem={legalEntityRefused || isPointProblem(problem) ? null : problem}
          // `initial` is not a prefill, it *is* the record being edited
          // (`organizationToFormValues(customer)`, from the detail route); its absence is
          // what "Nuovo cliente" means, and the list route passes none. Deriving the
          // mode from it rather than taking a third prop is what keeps the two from
          // ever disagreeing -- a `mode="edit"` next to no record, or a
          // `mode="create"` next to one, would be a contradiction this form could not
          // resolve. If a create dialog ever does get a prefilled `initial`, the
          // effect of guessing "edit" here is only that an untouched checkbox is
          // omitted rather than sent as `false`, which `renderFieldValue` already
          // reads as "No" anyway -- benign in the one direction it can be wrong.
          mode={isCreate ? 'create' : 'edit'}
        />

        <ContactPointsEditor
          points={drafts}
          onChange={(points) =>
            setValues((previous) => ({
              ...previous,
              native: { ...previous.native, [CONTACT_POINTS_KEY]: points },
            }))
          }
          errors={pointRows}
        />

        {showLegalEntityPicker ? (
          <div className="space-y-2">
            <Label htmlFor="customer-legal-entity">Azienda</Label>
            {/* Straight into `native`, not through `change`: that routes a key the
                active schema lists as a custom field into `custom`, and a tenant may
                well have named one `azienda_id` (CodeRabbit, PR #509). */}
            <Select
              value={shownLegalEntity}
              onValueChange={(value) =>
                setValues((previous) => ({
                  ...previous,
                  native: { ...previous.native, legal_entity_id: value },
                }))
              }
            >
              <SelectTrigger id="customer-legal-entity" className="w-full">
                <SelectValue placeholder="Seleziona…" />
              </SelectTrigger>
              <SelectContent>
                {legal_entity.legal_entities.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {legalEntityRefused ? (
              <p className="text-sm text-destructive">{legalEntityRefused}</p>
            ) : stranded && chosenLegalEntity === initialLegalEntity ? (
              <p className="text-sm text-muted-foreground">
                L’azienda di questo cliente non è più attiva: scegline una attiva per poter
                creare nuovi deal, documenti e fatture.
              </p>
            ) : isCreate && (chosenLegalEntity === undefined || chosenLegalEntity === null) ? (
              <p className="text-sm text-muted-foreground">
                Proposta dalla nazione del cliente: la fattura questa azienda, salvo tua scelta.
              </p>
            ) : null}
          </div>
        ) : null}

        {owners !== undefined ? (
          <div className="space-y-2">
            <Label htmlFor="customer-owner">Responsabile</Label>
            {/* Straight into `native`, like the azienda picker: a tenant's custom field
                may well be named `owner_id`. */}
            <Select
              value={chosenOwner ?? NO_OWNER}
              onValueChange={(value) =>
                setValues((previous) => ({
                  ...previous,
                  native: { ...previous.native, owner_id: value === NO_OWNER ? null : value },
                }))
              }
            >
              <SelectTrigger id="customer-owner" className="w-full">
                <SelectValue placeholder="Seleziona…" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_OWNER}>Nessuno</SelectItem>
                {owners.map((person) => (
                  <SelectItem key={person.id} value={person.id}>
                    {person.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">
              Chi riceve i promemoria su fatture e contratti di questo cliente.
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
