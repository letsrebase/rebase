import { useState } from 'react'
import { DynamicForm } from '@/components/DynamicForm'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
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
import { useLegalEntity, useLegalEntityProposal } from '@/lib/legalEntity'
import { isBlank, toFormValues, toRequestBody, type FormValues } from '@/lib/formValues'
import { useModules } from '@/lib/modules'
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
import { SEARCH_DEBOUNCE_MS } from '@/features/search/queries'
import { useDebounced } from '@/hooks/useDebounced'
import { useAddOrganizationRole, useOrganizationsByVat, type Organization } from './queries'
import {
  CUSTOMER_KIND_LABELS,
  CUSTOMER_KINDS,
  ROLE_LABELS,
  ROLE_ORDER,
  SUPPLIER_AREA_LABELS,
  SUPPLIER_AREAS,
  SUPPLIER_KIND_LABELS,
  SUPPLIER_KINDS,
  SUPPLIER_SUPPLIES,
  SUPPLIER_SUPPLIES_LABELS,
  normaliseVat,
  orderedRoles,
  rolesInWords,
  type OrganizationRole,
} from './roles'

const text = (key: string, label: string, required = false): FieldDefinition => ({
  key,
  label,
  type: 'text',
  required,
  options: [],
})

/** Who it is: the name and the VAT number, drawn on their own so the duplicate catch sits
 *  right under the number. */
const IDENTITY_FIELDS: FieldDefinition[] = [
  text('legal_name', 'Ragione sociale', true),
  text('vat_number', 'P.IVA'),
]

const COMMON_FIELDS: FieldDefinition[] = [
  text('tax_code', 'Codice fiscale'),
  text('sdi_code', 'Codice SDI'),
  text('certified_email', 'PEC'),
  text('address', 'Indirizzo'),
  text('postal_code', 'CAP'),
  text('city', 'Comune'),
  text('province', 'Provincia'),
  // Freeform text, not a `select`: OrganizationCreate/Update place no enum on either
  // field (plain `SafeStr`, max_length only) -- offering a client-side dropdown of
  // guessed options would let the UI reject a value the backend, and an MCP agent
  // writing through the same service, both accept without complaint.
  text('country', 'Nazione'),
  { key: 'website', label: 'Sito web', type: 'url', required: false, options: [] },
  text('status', 'Stato'),
]

/** «Come cliente»: the payment terms (REB-326) every due date to this customer is computed
 *  from (days empty means the fiscal profile's apply), and whether it withholds tax. */
const CUSTOMER_FIELDS: FieldDefinition[] = [
  { key: 'payment_days', label: 'Giorni di pagamento', type: 'number', required: false, options: [] },
  { key: 'payment_end_of_month', label: 'Scadenza a fine mese', type: 'checkbox', required: false, options: [] },
  { key: 'is_withholding_agent', label: 'Sostituto d’imposta', type: 'checkbox', required: false, options: [] },
]

/** «Come fornitore»: the terms it grants us. */
const SUPPLIER_FIELDS: FieldDefinition[] = [
  { key: 'supplier_payment_days', label: 'Giorni concessi dal fornitore', type: 'number', required: false, options: [] },
  { key: 'supplier_payment_end_of_month', label: 'Fine mese (fornitore)', type: 'checkbox', required: false, options: [] },
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

/** Every native column a `DynamicForm` draws: the type of a column decides how a cleared
 *  value is spelled (`clearedNativeValue`). */
const NATIVE_FIELDS: FieldDefinition[] = [
  ...IDENTITY_FIELDS,
  ...COMMON_FIELDS,
  ...CUSTOMER_FIELDS,
  ...SUPPLIER_FIELDS,
]

/** The columns a person picks from a list of words, each with a control of its own below:
 *  a `select` field of `DynamicForm` would submit the option's label, not its value. */
const ENUM_KEYS = ['customer_kind', 'supplier_kind', 'supplier_area', 'supplier_supplies'] as const
type EnumKey = (typeof ENUM_KEYS)[number]

// `legal_entity_id` is seeded and sent like a native column but drawn by its own picker below
// `DynamicForm`, the way the cost form draws `category_id`: the value is an id, and a
// `select` field would submit the option's label (REB-626).
// `owner_id` (REB-773) is the same kind of key: an id drawn by its own picker, only for the
// admin who may change it. `roles` is a set, drawn as checkboxes.
const SEEDED_KEYS = [
  ...NATIVE_FIELDS.map((field) => field.key),
  ...ENUM_KEYS,
  'roles',
  'legal_entity_id',
  'owner_id',
]

/** The columns an edit sends only when they changed: they are new to the form, so a record
 *  that predates them is never rewritten with their stored defaults. */
const SEND_IF_CHANGED = ['is_withholding_agent', 'supplier_payment_days', 'supplier_payment_end_of_month']

const CUSTOMER_KEYS = CUSTOMER_FIELDS.map((field) => field.key)
const SUPPLIER_KEYS = SUPPLIER_FIELDS.map((field) => field.key)

/** What the owner picker's «nessuno» option submits as: a `Select` item cannot hold `null`. */
const NO_OWNER = '__nessuno'
/** Likewise for an unset kind. */
const NO_CHOICE = '__nessuno'

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
function createValues(roles: readonly string[]): OrganizationFormValues {
  return { native: { country: 'IT', roles: [...roles] }, custom: {} }
}

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

function rolesOf(values: OrganizationFormValues | undefined): string[] {
  const roles = values?.native.roles
  return Array.isArray(roles) ? (roles as string[]) : []
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
  /** The roles a new organization starts with: the registry view the form was opened from
   *  («Nuovo fornitore» starts as a supplier). «Cliente» when left out. */
  defaultRoles?: readonly string[]
  /** The organization being edited, left out of the duplicate catch. */
  organizationId?: string
  /** Where to go once the organization a VAT number already belongs to has the role asked
   *  for (or when the person opens it): its page. */
  onOpenExisting?: (organization: Organization) => void
}

/** A select of words for one of the kind columns, with «Non indicato» for none. */
function KindSelect<V extends string>({
  id,
  label,
  value,
  options,
  onChange,
  error,
}: {
  id: string
  label: string
  value: unknown
  options: readonly { value: V; label: string }[]
  onChange: (next: V | null) => void
  error: string | null
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Select
        value={typeof value === 'string' && value !== '' ? value : NO_CHOICE}
        onValueChange={(next) => onChange(next === NO_CHOICE ? null : (next as V))}
      >
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_CHOICE}>Non indicato</SelectItem>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
    </div>
  )
}

function options<V extends string>(order: readonly V[], labels: Record<V, string>) {
  return order.map((value) => ({ value, label: labels[value] }))
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
  defaultRoles = ['customer'],
  organizationId,
  onOpenExisting,
}: Props) {
  const [values, setValues] = useState<OrganizationFormValues>(initial ?? createValues(defaultRoles))
  const isCreate = initial === undefined
  const purchasesOn = useModules().on('purchases')
  const roles = rolesOf(values)
  const initialRoles = rolesOf(initial)
  // A group of fields exists for a role the organization has: the customer's for a customer,
  // the supplier's for a supplier while `purchases` is on (the role stays on the row with the
  // module off, its fields are not drawn and nothing of them is sent).
  const customerShown = roles.includes('customer')
  const supplierShown = roles.includes('supplier') && purchasesOn
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

  // The duplicate catch: a VAT number that a live organization of the same country already
  // holds (design § 1, one record per company). Looked up once the typing settles; an edit
  // that leaves the number as it was has nothing to catch.
  const vatText = typeof values.native.vat_number === 'string' ? values.native.vat_number : ''
  const vatChanged = isCreate || vatText !== ((initial?.native.vat_number as string | null | undefined) ?? '')
  const settledVat = useDebounced(vatText.trim(), SEARCH_DEBOUNCE_MS)
  const vatKey = normaliseVat(settledVat)
  const lookup = useOrganizationsByVat(settledVat, open && vatChanged && vatKey.length >= 4)
  const duplicates =
    vatChanged && vatKey === normaliseVat(vatText)
      ? (lookup.data?.items ?? []).filter(
          (found) =>
            found.id !== organizationId &&
            normaliseVat(found.vat_number ?? '') === vatKey &&
            found.country.toUpperCase() === (country.trim() || 'IT').toUpperCase(),
        )
      : []
  const addRole = useAddOrganizationRole()

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
      setValues(initial ?? createValues(defaultRoles))
      setChecked(false)
      addRole.reset()
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

  /** Straight into `native`, not through `change`: that routes a key the active schema lists
   *  as a custom field into `custom`, and a tenant may well have named one `supplier_kind`. */
  function setNative(patch: Record<string, unknown>) {
    setValues((previous) => ({ ...previous, native: { ...previous.native, ...patch } }))
  }

  function toggleRole(role: OrganizationRole, on: boolean) {
    setNative({
      roles: ROLE_ORDER.filter((candidate) => (candidate === role ? on : roles.includes(candidate))),
    })
  }

  function submit() {
    if (pointsEdited && !pointsAreSendable(drafts, initialDrafts)) {
      setChecked(true)
      return
    }
    const hiddenGroupKey = (key: string) =>
      (CUSTOMER_KEYS.includes(key) && !customerShown) || (SUPPLIER_KEYS.includes(key) && !supplierShown)
    // `legal_entity_id` and `owner_id` are decided below from what their pickers show, not
    // from the seeded value: re-sending an unchanged id would be refused the day that
    // legal entity is deactivated.
    const body = toRequestBody(values, {
      fields: NATIVE_FIELDS,
      initial,
      isRenderedCustomKey,
      skipNative: (key) =>
        key === 'legal_entity_id' ||
        key === 'owner_id' ||
        key === CONTACT_POINTS_KEY ||
        key === 'roles' ||
        (ENUM_KEYS as readonly string[]).includes(key) ||
        hiddenGroupKey(key) ||
        (!isCreate && SEND_IF_CHANGED.includes(key) && values.native[key] === initial?.native[key]),
      // A number control hands back its text (`DynamicFieldRenderer` reads
      // `event.target.value`); the column is an integer and `OrganizationUpdate` is typed
      // `int | None`, so the digits are sent as the number they are.
      coerceNative: (key, value) =>
        NATIVE_FIELDS.find((field) => field.key === key)?.type === 'number' &&
        typeof value === 'string'
          ? Number(value)
          : value,
    })

    // The roles: always on a new organization, on an edit only when one was added.
    if (isCreate || roles.join() !== initialRoles.join()) body.roles = roles

    // The kind columns, each a value of the server's own list or nothing: a new one sends
    // what was chosen, an edit what changed, and `null` for one that was emptied.
    const kindKeys: EnumKey[] = [
      ...(customerShown ? (['customer_kind'] as const) : []),
      ...(supplierShown ? (['supplier_kind', 'supplier_area', 'supplier_supplies'] as const) : []),
    ]
    for (const key of kindKeys) {
      const now = values.native[key]
      const was = initial?.native[key]
      if (!isBlank(now)) {
        if (isCreate || now !== was) body[key] = now
      } else if (!isBlank(was)) {
        body[key] = null
      }
    }

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

  // The refusals the server pins on a control that is not a `DynamicForm` field are shown
  // under that control; every other one goes to the group that draws the field it names, so
  // it is said once.
  const refusedKey = problem?.field
  const ownRefusal = (key: string): string | null =>
    problem && refusedKey === key ? (fieldErrorFrom(problem)?.message ?? problem.detail) : null
  const legalEntityRefused = ownRefusal('legal_entity_id')
  const OWN_KEYS = ['legal_entity_id', 'roles', ...ENUM_KEYS]
  const ownRefused = refusedKey !== undefined && OWN_KEYS.includes(refusedKey)
  const groupFields: Record<'identity' | 'common' | 'customer' | 'supplier', FieldDefinition[]> = {
    identity: IDENTITY_FIELDS,
    common: [...COMMON_FIELDS, ...(isCreate ? [FIRST_NOTE_FIELD] : []), ...customFields],
    customer: customerShown ? CUSTOMER_FIELDS : [],
    supplier: supplierShown ? SUPPLIER_FIELDS : [],
  }
  const target =
    !problem || ownRefused || isPointProblem(problem)
      ? null
      : (['identity', 'customer', 'supplier'] as const).find((group) =>
          groupFields[group].some((field) => field.key === refusedKey),
        ) ?? 'common'
  const problemFor = (group: keyof typeof groupFields) => (target === group ? (problem ?? null) : null)

  const wantedRole: OrganizationRole | null =
    duplicates.length === 1
      ? (['supplier', 'customer'] as const).find(
          (role) => roles.includes(role) && !duplicates[0]!.roles.includes(role),
        ) ?? null
      : null
  const existing = duplicates.length === 1 ? duplicates[0]! : null
  const roleChoices = orderedRoles([...(['customer', 'supplier'] as const), ...roles.filter((role) => role === 'freelancer')])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <DynamicForm
          fields={groupFields.identity}
          values={{ ...nativeForForm, ...values.custom }}
          onChange={change}
          problem={problemFor('identity')}
          mode={isCreate ? 'create' : 'edit'}
        />

        {duplicates.length > 0 ? (
          <div role="status" className="space-y-2 border border-border bg-paper px-3 py-2 text-sm sm:ml-auto sm:w-1/2">
            {existing ? (
              <>
                <p>{`Esiste già: ${existing.legal_name} (${rolesInWords(existing.roles)})`}</p>
                <div className="flex flex-wrap items-center gap-2">
                  {wantedRole ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={addRole.isPending}
                      onClick={() =>
                        addRole.mutate(
                          { id: existing.id, roles: orderedRoles([...existing.roles, wantedRole]) },
                          {
                            onSuccess: (updated) => {
                              onOpenChange(false)
                              onOpenExisting?.(updated)
                            },
                          },
                        )
                      }
                    >
                      {`Aggiungi il ruolo ${ROLE_LABELS[wantedRole]}`}
                    </Button>
                  ) : (
                    <Button type="button" variant="outline" size="sm" onClick={() => onOpenExisting?.(existing)}>
                      Apri la scheda
                    </Button>
                  )}
                </div>
                {addRole.isError ? (
                  <p className="text-destructive">{toProblem(addRole.error).detail}</p>
                ) : null}
              </>
            ) : (
              <p>
                {`Questa partita IVA è già su più anagrafiche (${duplicates.map((found) => found.legal_name).join(', ')}): non si capisce quale sia, quindi non si può aggiungere un ruolo.`}
              </p>
            )}
          </div>
        ) : null}

        {purchasesOn ? (
          <fieldset className="space-y-2">
            <legend className="mb-2 text-sm font-medium">Ruoli</legend>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              {roleChoices.map((role) => {
                const held = !isCreate && initialRoles.includes(role)
                return (
                  <label key={role} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={roles.includes(role)}
                      // A role is added from here, never taken away (the brief's non-goal).
                      disabled={held || role === 'freelancer'}
                      onCheckedChange={(checked) => toggleRole(role as OrganizationRole, checked === true)}
                    />
                    {ROLE_LABELS[role as OrganizationRole]}
                  </label>
                )
              })}
            </div>
            {ownRefusal('roles') ? <p className="text-sm text-destructive">{ownRefusal('roles')}</p> : null}
          </fieldset>
        ) : null}

        <DynamicForm
          fields={groupFields.common}
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
          // Withheld when the server blamed a control with its own line (the azienda picker,
          // the roles, the kinds) or a contact point: `DynamicForm` would render the message a
          // second time as a raw banner next to it.
          problem={problemFor('common')}
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

        {customerShown ? (
          <section className="space-y-4" aria-labelledby="organization-as-customer">
            <h3 id="organization-as-customer" className="text-sm font-semibold">
              Come cliente
            </h3>
            <div className="grid gap-5 sm:grid-cols-2">
              <KindSelect
                id="organization-customer-kind"
                label="Tipo di cliente"
                value={values.native.customer_kind}
                options={options(CUSTOMER_KINDS, CUSTOMER_KIND_LABELS)}
                onChange={(next) => setNative({ customer_kind: next })}
                error={ownRefusal('customer_kind')}
              />
            </div>
            <DynamicForm
              fields={groupFields.customer}
              values={{ ...nativeForForm, ...values.custom }}
              onChange={change}
              problem={problemFor('customer')}
              mode={isCreate ? 'create' : 'edit'}
            />
          </section>
        ) : null}

        {supplierShown ? (
          <section className="space-y-4" aria-labelledby="organization-as-supplier">
            <h3 id="organization-as-supplier" className="text-sm font-semibold">
              Come fornitore
            </h3>
            <div className="grid gap-5 sm:grid-cols-2">
              <KindSelect
                id="organization-supplier-kind"
                label="Tipo fiscale"
                value={values.native.supplier_kind}
                options={options(SUPPLIER_KINDS, SUPPLIER_KIND_LABELS)}
                onChange={(next) =>
                  // Where it is and what it supplies mean something for a foreign supplier
                  // only: leaving that kind lets go of both, and the server would refuse them.
                  setNative(
                    next === 'foreign'
                      ? { supplier_kind: next }
                      : { supplier_kind: next, supplier_area: null, supplier_supplies: null },
                  )
                }
                error={ownRefusal('supplier_kind')}
              />
              {values.native.supplier_kind === 'foreign' ? (
                <>
                  <KindSelect
                    id="organization-supplier-area"
                    label="Area"
                    value={values.native.supplier_area}
                    options={options(SUPPLIER_AREAS, SUPPLIER_AREA_LABELS)}
                    onChange={(next) => setNative({ supplier_area: next })}
                    error={ownRefusal('supplier_area')}
                  />
                  <KindSelect
                    id="organization-supplier-supplies"
                    label="Fornitura"
                    value={values.native.supplier_supplies}
                    options={options(SUPPLIER_SUPPLIES, SUPPLIER_SUPPLIES_LABELS)}
                    onChange={(next) => setNative({ supplier_supplies: next })}
                    error={ownRefusal('supplier_supplies')}
                  />
                </>
              ) : null}
            </div>
            <DynamicForm
              fields={groupFields.supplier}
              values={{ ...nativeForForm, ...values.custom }}
              onChange={change}
              problem={problemFor('supplier')}
              mode={isCreate ? 'create' : 'edit'}
            />
          </section>
        ) : null}

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
              onValueChange={(value) => setNative({ legal_entity_id: value })}
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
              onValueChange={(value) => setNative({ owner_id: value === NO_OWNER ? null : value })}
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
          {/* A second record for a company that is already there is not offered. */}
          <Button onClick={submit} disabled={busy || duplicates.length > 0}>
            {busy ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
